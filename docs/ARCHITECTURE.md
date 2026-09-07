# Architecture

## The problem

Home Assistant has no `notify.*` that speaks. Voice goes through
`tts.speak`, an **action** registered on `tts.*` entities
(`homeassistant/components/tts/__init__.py`, `component.async_register_entity_service("speak", ...)`,
handled by `TextToSpeechEntity.async_speak` in
`homeassistant/components/tts/entity.py`). Actions are not notify services:
the core `alert` integration's `notifiers:` list, and any blueprint or
automation written against "call a notify service", cannot target
`tts.speak` directly -- there is no `notify.speak_on_kitchen` to put in that
list. Cast Notifier closes that gap by wrapping `tts.speak` in one
`notify.*` service per configured Cast player.

## What `tts.speak` actually does

Reading `TextToSpeechEntity.async_speak` (`homeassistant/components/tts/entity.py`,
`async_speak`):

```python
async def async_speak(
    self,
    media_player_entity_id,
    message,
    cache,
    language=None,
    options=None,
) -> None:
    await self.hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: media_player_entity_id,
            ATTR_MEDIA_CONTENT_ID: generate_media_source_id(...),
            ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
            ATTR_MEDIA_ANNOUNCE: True,
        },
        blocking=True,
        context=self._context,
    )
```

Two things follow directly from this:

1. **`tts.speak` always calls `media_player.play_media` with `announce=True`**,
   whether or not the target `media_player` supports announcing. It is the
   target entity's own `async_play_media` implementation that decides what
   to do with `announce`.
2. **The call returns as soon as `media_player.play_media` returns** -- not
   once the speaker has gone quiet again. For an entity that implements the
   announce contract properly (ducking playback, waiting for the TTS clip to
   finish, restoring playback), that return *is* the end of the
   announcement. For an entity that does not, `play_media` returns as soon
   as playback of the new clip has *started*.

## Cast does not support announce

Checked in `homeassistant/components/cast/media_player.py` (2026.9.1),
`CastMediaPlayerEntity.supported_features` (around line 1013):

```python
@property
@override
def supported_features(self) -> MediaPlayerEntityFeature:
    support = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.TURN_ON
    )
    ...
```

`MediaPlayerEntityFeature.MEDIA_ANNOUNCE` (defined in
`homeassistant/components/media_player/const.py`) never appears in this
property, under any branch. `CastMediaPlayerEntity.async_play_media`
(same file, `async_play_media`) also never reads `kwargs.get("announce")` --
the flag `tts.speak` always sends is silently ignored. So calling
`tts.speak` against a Cast player:

- interrupts whatever the player was doing and switches it to the TTS
  clip, at the player's **current volume** (no ducking);
- returns almost immediately, once the cast command to start that clip has
  been issued -- not once the clip has finished playing;
- never resumes whatever was playing before.

This is why Cast Notifier does not simply delegate to `tts.speak` and call
it done: on Cast, that call alone gives you a spoken message at whatever
volume happens to be set, with no way to know when it is safe to act again
(e.g. restore volume), and no way to duck a podcast that was playing.

## What Cast Notifier does about it (`speaker.py`)

`CastSpeaker.async_speak` validates the call's `data` payload, applies the
deny list, then takes a per-speaker `asyncio.Lock` and, for a call carrying
a volume (from the config entry's `volume` option or a per-call
`data.volume` override):

1. Reads the player's current `volume_level` (`ATTR_MEDIA_VOLUME_LEVEL`,
   `homeassistant/components/media_player/const.py`) and remembers it.
2. Calls `media_player.volume_set` (`SERVICE_VOLUME_SET`,
   `homeassistant/const.py`) to the requested volume.
3. Records a *fingerprint* of what the player is doing: its `state` plus
   `ATTR_MEDIA_CONTENT_ID`, `ATTR_APP_ID` and `ATTR_MEDIA_TITLE`.
4. Calls `tts.speak` as above.
5. Waits for the announcement to run its course (see below).
6. If `restore_volume` (default `True`), restores the volume from step 1.

Steps 4 to 6 are one `try`/`finally`: a `tts.speak` that raises -- an
unavailable engine, a dead player -- must never leave a speaker stuck at
the announcement volume, so the restore happens on the way out and the
error is then re-raised to the caller.

If no volume is configured for the call, none of this runs: Cast Notifier
just calls `tts.speak` and returns, exactly like calling the action
directly, only reachable as a `notify.*` service.

### Two gates before any volume is touched

`CastSpeaker` reads `ATTR_SUPPORTED_FEATURES` from the target's state
attributes and manages volume only when **both** hold:

- `MediaPlayerEntityFeature.MEDIA_ANNOUNCE` is **absent**. An entity that
  declares announce support is expected to duck and wait inside its own
  `async_play_media`, in which case managing volume manually would fight
  the platform instead of helping it. Against a real Cast player this is
  always true (see above); the check exists so this integration, or a
  future Cast release, benefits automatically once/if that changes.
- `MediaPlayerEntityFeature.VOLUME_SET` is **present**. Cast *groups* and
  fixed-output devices do not have a settable volume, and calling
  `media_player.volume_set` on them raises.

Volume management is a convenience, never a precondition: a `volume_set`
that fails anyway (a transient error, a player that lies about its
features) is logged and the message is spoken regardless.

### Waiting for the announcement (`_async_wait_for_playback_end`)

The wait is a heuristic, and deliberately a bounded one. It runs in two
phases against the fingerprint recorded in step 3, watching
`homeassistant/helpers/event.py::async_track_state_change_event`:

1. **Leave**: wait until the player stops matching the fingerprint, i.e.
   the announcement started. Capped at `ANNOUNCEMENT_START_TIMEOUT` (5s).
2. **Return**: wait until it matches the fingerprint again, i.e. the
   announcement ended. Capped by what remains of `PLAYBACK_TIMEOUT` (30s),
   which bounds both phases together.

Two design points, both learned the hard way:

- **`state` alone is not enough.** A Cast player that was playing music is
  `playing` before the announcement and `playing` during it. Only the media
  identity changes, which is why `media_content_id` (a fresh
  `media-source://tts/...` URL for every clip), `app_id` and `media_title`
  are part of the fingerprint.
- **Phase 1 needs its own, much shorter budget.** Home Assistant may never
  see the transition at all -- a short clip can start and finish between
  two state updates. With a single shared deadline, that case burned the
  full 30s on every call. Bounding phase 1 at 5s means the worst case is
  "restore the volume 5s later than ideal", not "block the caller for half
  a minute". If phase 1 times out, phase 2 returns immediately: the player
  never visibly left its previous state, so it is already back.

### One announcement at a time

Each `CastSpeaker` holds an `asyncio.Lock`, taken after validation and the
deny-list check and released once the volume is restored. Without it, two
overlapping calls both read "the previous volume" -- and the second one
reads the *announcement* volume the first one just set, then dutifully
"restores" it, leaving the speaker permanently quiet. Validation and the
deny list run outside the lock so a refused call never queues behind an
announcement in progress.

## Input contract

`notify.cast_<name>`, and the `NotifyEntity`'s `send_message`, accept:

- `message`: the text to speak (required).
- `data.tts_entity`, `data.language`, `data.voice`, `data.volume`: per-call
  overrides of the matching config entry option.
- `data.source_entity`: the entity a message is *about*
  (e.g. `alarm_control_panel.home`). Never spoken; used only to enforce
  `deny_domains`.
- `data.priority`: how urgent the caller considers this message. Exactly
  one of `info`, `normal`, `high`, `critical`, lower case, matched
  literally; anything else is refused as invalid data. Only `critical`
  acts -- it bypasses quiet hours -- and the other three are accepted and
  ignored, because this is the key a wider notification layer forwards
  untouched to every notifier it fans out to. Never spoken. See
  [ADR-0002](ADR/0002-priority-vocabulary.md) for why the match is exact
  rather than lenient.

`data` is validated by `SPEAK_DATA_SCHEMA` (`speaker.py`) before any of it
is read: `source_entity` must be an entity id, `volume` a float in
`[0, 1]`, `tts_entity` an entity id in the `tts` domain, `language` a
string, `voice` a string or a mapping, `priority` one of the four values
above. Anything else raises
`CastNotifierInvalidData` and nothing is spoken -- a malformed automation
gets a message naming the problem instead of an `AttributeError` from
somewhere inside the speaking path. Unknown keys are allowed through and
ignored, so a `data` payload shared with another notifier does not break
this one.

The `NotifyEntity` surface has no `data` payload (`NotifyEntityFeature`
does not define one), so per-call overrides and `deny_domains` enforcement
are only reachable through the legacy service today.

### Refusals raise -- ADR-015 of the suite

Every failure reaches the caller; `notify.py::_async_speak` swallows
nothing. A `deny_domains` refusal (`CastNotifierRefused`) and a malformed
`data` payload (`CastNotifierInvalidData`) are both
`ServiceValidationError`s -- translated through the `exceptions` section of
`strings.json`, so the caller sees a real message and Home Assistant shows
it without a traceback -- raised once `CastSpeaker` has restored any volume
it changed. A failing `tts.speak` or an unexpected error surfaces as a
`HomeAssistantError` the same way.

A refusal is *also* logged at WARNING, by `CastSpeaker` itself, before it
is raised: the log line carries the operator-facing context (which entity,
which player, the whole deny list) that a caller-facing message should not.
The one thing that must never happen is the earlier behaviour -- a refusal
logged and swallowed, answering `POST /api/services/notify/cast_kitchen`
with a silent HTTP 200 for a message nobody ever heard. See
[`known-issues.md`](known-issues.md) for what that double reporting costs
under core `alert`.

## Quiet hours

Three options: `quiet_start`, `quiet_end` (both `HH:MM:SS`, from a
`TimeSelector`) and an optional `quiet_volume`. `is_within_quiet_hours`
(`speaker.py`) is a pure function of the two bounds and a
`datetime.time`, which is why every boundary is unit tested rather than
inferred from a frozen clock.

The window is **half-open**, `[start, end)`, so an end of `07:00:00`
means "quiet until 07:00, speaking from 07:00", and it may **cross
midnight**, which is the normal case: `22:00` -> `07:00` reads as "at or
after start, or before end". It is evaluated against the instance's local
time (`homeassistant/util/dt.py::now`, i.e. `hass.config.time_zone`), not
UTC.

Quiet hours are off whenever the window cannot be read as a real
interval: a bound missing, a bound unparseable, or the two bounds equal.
That last one is a decision, not an oversight -- a zero-length window
could just as well be read as "always quiet", and silently muting a
notifier for 24 hours because someone set the same time twice is the
worst available reading. A half-configured window is refused by the
options flow (`quiet_hours_incomplete`) rather than stored and ignored
for good.

Inside the window:

- with a `quiet_volume`, the message is spoken at that volume;
- without one, it is **refused**: `CastNotifierQuietHours`, a translated
  `ServiceValidationError` like the other two (ADR-015), plus an INFO log
  line carrying the reason `quiet_hours` and the window. INFO, not
  WARNING: the configuration is doing exactly what it was told to do.

Two escapes, in this order of precedence:

1. `data.priority: "critical"` -- that exact string -- bypasses the check
   entirely, before the window is even evaluated. A water leak at 3am is
   what a notifier is for. The comparison can be a plain `==` because
   `SPEAK_DATA_SCHEMA` has already refused every spelling that is not in
   the vocabulary.
2. A per-call `data.volume` wins over `quiet_volume`. A caller that names
   a volume has said something about *this* message; `quiet_volume` is a
   rule about this time of day.

So the full volume precedence is `data.volume` > `quiet_volume` (in the
window) > the entry's `volume`. The check runs before the per-player
lock, like the deny list, so a refused call never queues behind an
announcement in progress.

That placement has a consequence worth stating: **the window is evaluated
when the call arrives, not when the message is spoken.** A call that
lands at 21:59:59 on a `22:00`-`07:00` window is decided as daytime, and
if it then waits behind an announcement already in progress -- up to
`PLAYBACK_TIMEOUT`, 30 seconds -- it is spoken after 22:00, at full
volume. The reverse holds too: a call accepted at 06:59:59 keeps the
`quiet_volume` it was granted even if it speaks at 07:00:01.

The alternative -- evaluating inside the lock -- trades that for a worse
one: a caller would then block for up to 30 seconds before being told its
message was refused, and a refusal is precisely the answer that should
come back immediately (ADR-015). The window is a 30-second-fuzzy boundary
on a rule measured in hours; a refusal that takes 30 seconds to arrive is
a bug in every automation that waits for it.

## The volume timeline

`speaker.py` records an `AnnouncementTimeline` for every announcement:
five volume readings, each with a UTC timestamp, logged at DEBUG and
exposed through the entry's diagnostics as `last_announcement`.

| step | what it answers |
|---|---|
| `volume_before` | what the player was at when the call arrived |
| `volume_requested` | what Cast Notifier decided to speak at |
| `volume_after_set` | what the state attribute said once `volume_set` returned |
| `volume_while_playing` | what the player reported while it was `playing` |
| `volume_restored` | what it was left at |
| `volume_restore_failed` | recorded instead, when the restore was refused |

The last two are exclusive: an announcement records one or the other, so
a speaker left at announcement volume says so instead of reporting a
restore that never happened (`media_player.volume_set` failing is
reported, not raised -- see "Two gates before any volume is touched").

The fourth is the one that does not exist anywhere else. `volume_set`
returning, and the attribute just after it, both describe what Home
Assistant *asked for*; a Cast device can report something else once it
starts playing. The first real announcement on this integration played at
an observed 0.55 for a configured 0.40, and nothing in the log said so.
It is captured by a state listener registered for the duration of the
call (`_async_observe_playing_volume`), which keeps only the first
`playing` observation so a long clip cannot grow the timeline.

Bounded to one announcement, deliberately: this is a diagnostic aid for
"why was the message I just heard that loud?", not a history. A second
announcement replaces the first.

## The deny list: an opt-in safety net

`deny_domains` defaults to `alarm_control_panel` and `lock`. When a call
carries `data.source_entity` and that entity's domain is in the list, the
message is refused -- logged as a warning, and `CastNotifierRefused` raised
to the caller (see above) -- before it ever reaches `tts.speak`. Matching is
case-insensitive on both sides (`casefold()` at parse time in the config
flow and again at comparison time, so an entry stored before that
normalization still behaves).

**Its scope is exactly the `data.source_entity` a caller chose to
declare.** It is a safety net, not a guarantee, and per project doctrine
ADR-010 it is documented as one:

- it never inspects the message text, so a message *about* the alarm that
  does not declare `source_entity` is spoken;
- it is unreachable from the `NotifyEntity` surface, which has no `data`;
- it is not a boundary against anyone who can already call services on the
  instance.

What it *is* good for: making "never announce the alarm" a setting that an
automation opts into by naming its subject, so a wiring mistake in one
automation is caught by configuration rather than by review. The two
defaults are the whole reason it exists; the options flow lets a deployment
extend the list.

## Why both a legacy service and an entity

Home Assistant's `alert` integration lists `notifiers:` by legacy
`notify.*` service name; there is no way to point `alert` at a
`NotifyEntity` instead. So `notify.cast_<name>` (registered through Home
Assistant's discovery helper, the same mechanism `mobile_app` uses for its
own per-device services -- see `homeassistant/components/notify/legacy.py`,
`async_setup_legacy.async_setup_platform`) exists purely for `alert`
compatibility and for scripts/blueprints written against a plain service
name. The `NotifyEntity` is the forward-looking surface. Both share one
`CastSpeaker` instance stored on `entry.runtime_data`, so behavior never
diverges between them.

### Retracting the legacy service on unload

Home Assistant does **not** clean up a legacy notify service when a config
entry unloads. `notify/legacy.py::async_reset_platform` -- the only code
that calls `BaseNotificationService.async_unregister_services` and drops
the instance from `hass.data[NOTIFY_SERVICES]` -- is reached from
`homeassistant/helpers/reload.py` alone, i.e. from a YAML reload. Nothing
in `config_entries.py` goes near it.

So `async_setup_entry` registers an `entry.async_on_unload` callback that
does it by hand: `hass.services.async_remove("notify", "cast_<name>")`,
then removes this entry's `CastNotificationService` from
`hass.data[NOTIFY_SERVICES]["cast_notifier"]` (deleting the key when the
last one goes). Two bugs fall out of not doing this:

- deleting an entry left a `notify.cast_<name>` bound to a dead speaker;
- **no options change ever took effect on the legacy service**, because
  `async_register_services` starts with
  `if self.hass.services.has_service(DOMAIN, self._service_name): return`.
  The reload re-ran `async_get_service` and built a fresh
  `CastNotificationService`, and the early return threw it away in favour
  of the stale one. Retracting the service on unload is what makes the
  reload effective.

`hass.services.async_remove` by name, and not the service object's own
`async_unregister_services()`: that method is a coroutine, and
`entry.async_on_unload` callbacks are synchronous, so calling it would mean
firing a task nothing waits for -- the entry could be set up again before
the old service was gone. It also reads `self._service_name`, a private
attribute that exists only once `async_register_services` has run, which is
not guaranteed at unload time. The removal is guarded by
`hass.services.has_service(...)` so an entry whose discovery never
completed does not log "Unable to remove unknown service" on its way out.

The discovery that registers the service is tied to the entry
(`entry.async_create_task`) rather than to `hass`:
`ConfigEntry.async_unload` awaits the entry's own tasks
(`_async_process_on_unload`), so the discovery cannot still be running
against an entry that no longer exists.

That is not enough on its own, because the *registration* happens one hop
further away. `discovery.async_load_platform` ends in
`async_dispatcher_send_internal` (`helpers/dispatcher.py`), which runs a
coroutine listener through `hass.async_run_hass_job` -- a task nobody owns
and nothing awaits. So `notify/legacy.py::async_setup_platform` can call
`async_get_service`, and then `async_register_services`, *after* the unload
callback has already looked for a service that did not exist yet: the entry
goes away and leaves a `notify.cast_<name>` bound to a dead speaker with
nothing left to retract it. `CastNotifierRuntimeData.unloaded` closes that
window: the unload callback sets it synchronously, and both
`async_get_service` and `CastNotificationService.async_register_services`
(overridden for exactly this) bail out when it is set. Core's
`async_register_services` does not await before registering, so the check
cannot itself be raced.

## Entities, devices and naming

Each config entry registers one service device (`DeviceInfo` with
`identifiers={(DOMAIN, entry.entry_id)}`, `DeviceEntryType.SERVICE`) named
after the entry -- which the config flow titles after the Cast player. The
`NotifyEntity` sets `_attr_has_entity_name = True` and `_attr_name = None`,
so it inherits the device's name and lands on `notify.<entry title>`.

Before that, every entry hard-coded `_attr_name = "Cast Notifier"` and no
device at all: a second entry produced a second entity with the same
friendly name, colliding on `notify.cast_notifier` and indistinguishable in
the UI.

There **is** an `_attr_translation_key` (`announcement`), and it exists
for `icons.json` alone -- it is what lets the entity have an icon without
a hard-coded `_attr_icon` (the `icon-translations` rule). It cannot leak
into the name: `Entity._name_internal`
(`homeassistant/helpers/entity.py`) opens with
`if hasattr(self, "_attr_name"): return self._attr_name`, so a class that
sets `_attr_name = None` never reaches the translation lookup at all.
`strings.json` has no `entity` section either, so there is no name to
find. A test pins the entity's friendly name to its device's.

### The legacy service name

`notify.cast_<slugify(entry.title)>`, resolved in `__init__.py`
(`_base_service_name` / `_async_service_name`) and handed to the legacy
platform as `CONF_NAME` in the discovery payload, which
`notify/legacy.py::async_setup_legacy.async_setup_platform` slugifies
again into the final service name.

It used to come from the `media_player` **entity id**, which was wrong in
a way only a real installation showed: a player Home Assistant had
auto-suffixed (`media_player.kitchen_2`) produced `notify.cast_kitchen_2`
for an entry titled "Kitchen", and nothing in the UI explained where that
`_2` came from. The title is what the user sees, and the only part of the
entry they can change.

Renaming the entry therefore renames the service, with no restart:
`async_update_entry` fires the entry's update listeners for a changed
`title` exactly as it does for changed `options`
(`homeassistant/config_entries.py`, `_async_update_entry` ->
`_async_save_and_notify`), and the listener reloads the entry, which
retracts the old service and registers the new one.

Two entries can legitimately share a title, and a service name has to be
unique, so duplicates are numbered `_2`, `_3`, ... The first entry set up
keeps the plain slug; the next one to want the same slug takes the first
free number. Ignored entries are skipped (they never set up, so they
never own a service); disabled ones are counted, so enabling or disabling
an entry cannot renumber its neighbours.

**The result is frozen in `entry.data`** (`service_name`, plus the
`service_name_base` it was derived from) the first time the entry is set
up, and reused verbatim from then on. Only a change of title -- which
changes the base -- makes the integration compute a name again. This is
what makes the assignment survive a restart, an upgrade, and anything the
*other* entries do: deleting the entry that held the plain slug no longer
promotes its neighbour, and Home Assistant setting a domain's entries up
concurrently (`homeassistant/setup.py`, `asyncio.gather` over
`entry.async_setup_locked`) cannot shuffle them either.

A recomputed name skips every name another entry has claimed -- whether
that entry is loaded (`hass.data[SERVICE_OWNERS]`) or merely stored
(`entry.data`) -- and every `notify.*` name a *different* integration
already serves. It has to: `BaseNotificationService.async_register_services`
returns early when `hass.services.has_service(DOMAIN, self._service_name)`
is already true (`homeassistant/components/notify/legacy.py:312`,
2026.9.1). An entry that claimed a name someone else had would register
nothing at all, believe it owned the service anyway, and delete that
service on unload. For the same reason the unload callback removes
`notify.<name>` only when `hass.data[SERVICE_OWNERS]` says this entry is
the owner.

An entry created before 0.2.0 has no stored name; the first setup after
the upgrade picks one -- in creation order, so several such entries come
out the same whichever of them is set up first -- and freezes it.

This was a breaking change in 0.2.0, with no alias for the old names: an
integration answering to two names is one nobody can reason about, and
the old name was the accident. See the README's upgrade notes.

## Config entry versioning

`VERSION = 1`, `MINOR_VERSION = 1`, and `async_migrate_entry` exists as a
no-op returning `True`. There is nothing to migrate yet; the point is that
the first schema change ships as a migration instead of as an entry Home
Assistant refuses to load.

## `integration_type` and `iot_class`

`helper` and `calculated`. Cast Notifier owns no device and no connection:
it is a wrapper around service calls to entities Home Assistant already
has. It was declared `device`/`local_push` at first, which was wrong on
both counts -- the "device" is someone else's, and nothing is pushed to
this integration by anything.

## Config entry model

One config entry = one Cast player. `entry.data` holds only
`media_player` (the entry's identity: `async_set_unique_id(media_player)` +
`_abort_if_unique_id_configured()` prevent configuring the same player
twice). Everything else -- `tts_entity`, `language`, `voice`, `volume`,
`restore_volume`, `announce_prefix`, `deny_domains`, `quiet_start`,
`quiet_end`, `quiet_volume` -- lives in `entry.options` and is editable
through the options flow without deleting and re-adding the entry.
Changing options reloads the entry, which retracts and re-registers the
legacy service (see above) and rebuilds the `CastSpeaker` with the new
settings.

The two quiet-hours bounds are the only options that carry no schema
`default`: `selector.TimeSelector` validates with `cv.time`
(`homeassistant/helpers/selector.py`), which refuses the empty string, so
"not configured" cannot be expressed as `default=""`. They are prefilled
with suggested values instead
(`data_entry_flow.py::add_suggested_values_to_schema`).

The user step also refuses a `media_player` whose `supported_features`
lacks `MediaPlayerEntityFeature.PLAY_MEDIA`
(`homeassistant/components/media_player/const.py`), with the form error
`player_cannot_play_media`. Everything this integration does ends in
`media_player.play_media`, so such a player can never speak: without the
check, the entry is created happily and every announcement fails with
`ServiceNotSupported` (as one did on the dev instance). The check is
permissive where it cannot know: a player with no state, or with no
`supported_features` attribute at all, is accepted.

Known limitation: the unique ID is the `media_player` **entity id**, not
its entity registry id. Renaming the player's entity id therefore orphans
the entry rather than following the rename. The registry id would fix that,
but the picked entity is not guaranteed to be registered at all (a template
or YAML `media_player` is not), so the entity id stays the identity. Rename
the player before configuring it, or delete and re-add the entry.

## What this is not

Cast Notifier has no dependency on, and no awareness of, any other
notification project (e.g. a per-person routing layer). It is a single,
independent `notify.*` per Cast player -- nothing more.
