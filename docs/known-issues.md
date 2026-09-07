# Known issues

Behaviours that are understood, deliberate, and still worth knowing about
before they surprise someone in a log.

## A refusal under core `alert` produces two log lines

Since 0.1.1, a `deny_domains` refusal and an invalid `data` payload are
raised to the caller as a translated `ServiceValidationError`, on top of
being logged at WARNING by `CastSpeaker`
([ADR-0003](ADR/0003-refusals-raise.md)).

Core's `alert` integration calls its notifiers **without** `blocking`
(`homeassistant/components/alert/entity.py`,
`AlertEntity._send_notification_message` ->
`hass.services.async_call(NOTIFY_DOMAIN, target, msg_payload, context=...)`,
no `blocking=True`; only `ServiceNotFound` is caught there). Nothing awaits
the call, so nobody is there to receive the exception:
`HomeAssistant._run_service_call_catch_exceptions` (`homeassistant/core.py`)
logs it instead. The result, for one refused `alert`, is two entries:

```
WARNING (MainThread) [custom_components.cast_notifier.speaker] Refusing to
  speak a message about alarm_control_panel.home on media_player.kitchen:
  domain 'alarm_control_panel' is in deny_domains ['alarm_control_panel', 'lock']
ERROR (MainThread) [homeassistant.core] Error executing service:
  <ServiceCall notify.cast_kitchen ...>
```

This is noise, not a malfunction, and it is the lesser of the two evils:
the alternative -- swallowing the refusal so the log stays tidy -- answers
the caller with a success for a message that was never spoken. An
automation or script that calls `notify.cast_<name>` with `blocking: true`
sees the error where it belongs, in its own trace, and logs only the
WARNING.

Nothing to do about it here: the double reporting comes from how `alert`
calls its notifiers, not from this integration.

## A `NotifyEntity` call cannot be refused by `deny_domains`

`NotifyEntityFeature` defines no `data` payload, so the modern entity
surface has no `source_entity` to check. Automations that want the deny
list must call the legacy `notify.cast_<name>` service. See
[`ARCHITECTURE.md`](ARCHITECTURE.md), "The deny list: an opt-in safety net".

## Renaming a Cast player's entity id orphans its entry

The config entry's unique ID is the `media_player` entity id, not its
entity registry id (which a template or YAML `media_player` does not have).
Rename the player before configuring it, or delete and re-add the entry
afterwards.

Note that this is about the *player's* entity id, not the entry's title.
Renaming the **entry** is supported and, since 0.2.0, renames its
`notify.cast_<name>` service with it.

## A renamed entry can end up with a numbered service name

Since 0.2.0 the service name comes from the entry title, and duplicates
are numbered: two entries titled "Speaker" own `notify.cast_speaker` and
`notify.cast_speaker_2`. Each entry's name is then frozen in its
`entry.data`, so nothing another entry does can move it -- deleting the
first of those two leaves the second on `notify.cast_speaker_2`.

The one case where a rename does not give the obvious name is a rename
*onto* a title another entry already uses: rename "Kitchen" to "Dining"
while another entry is already called "Dining", and the renamed entry
gets `notify.cast_dining_2`, not `notify.cast_dining`. The alternative --
taking the name -- is not an option: core registers nothing when the name
is already in use (`homeassistant/components/notify/legacy.py:312`), so
the renamed entry would be mute while believing otherwise, and would
delete the other entry's service when unloaded.

The new name is visible in Developer tools -> Actions under `notify`, and
in the entry's diagnostics as `service_name`. Give two players two
different titles and the situation never arises.

A second case needs a disabled entry. Renaming a **disabled** entry away
from a title does not update its stored `service_name`: the update
listener that would recompute it is only registered during setup, and a
disabled entry never sets up, so the stale name sits in `entry.data`
untouched. If another entry is then renamed *onto* the old title, it
finds that stale name still "taken" and gets `_2` instead of the plain
slug -- even though nothing visibly answers to the plain name any more.
The plain name becomes free again as soon as the disabled entry is
re-enabled and sets up, which recomputes its (now different) name and
drops the stale one. This self-heals on the next rename of the entry
stuck on `_2`: recomputing then finds the plain name free and takes it.

## A quiet-hours refusal is invisible unless the caller waits

Like every other refusal, a message blocked by quiet hours raises
([ADR-0003](ADR/0003-refusals-raise.md)). It is logged at INFO with the
reason `quiet_hours`, not at WARNING: the configuration is doing what it
was told to do, and a nightly
alert would otherwise fill the log with warnings about working as
intended. An operator who wants to see them has to be logging at INFO, or
call with `blocking: true` and read the error.

## A message can cross the edge of the quiet window

Quiet hours are decided when the call arrives, not when the message is
actually spoken (`speaker.py`, `async_speak`: the check runs before the
per-player lock, like the deny list). Announcements on one player are
serialized, and one can wait up to `PLAYBACK_TIMEOUT` -- 30 seconds --
for the previous one to finish. A message accepted at 21:59:59 for a
window starting at 22:00 can therefore be heard, at full volume, just
after 22:00; one accepted at 06:59:59 can be heard at `quiet_volume`
just after 07:00.

Deliberate: evaluating inside the lock would make a *refusal* take up to
30 seconds to reach a caller that is waiting for it, which is worse than
a boundary that is fuzzy by half a minute. See
[`ARCHITECTURE.md`](ARCHITECTURE.md), "Quiet hours".

## `volume_while_playing` can be missing from the timeline

The volume timeline's fourth reading comes from a state listener watching
for the player to report `playing`. Home Assistant may never see that
transition -- a short clip can start and finish between two state updates
from a Cast device, which is the same reason the "announcement started"
phase of the playback wait is bounded at 5s. When that happens the step
is simply absent from `last_announcement`, which is itself informative:
Home Assistant never observed the announcement at all.

## Final findings, not fixed (2026-09-07, repository archived)

Measured on a real installation (Home Assistant 2026.9.1, a Google Home
Mini as the `cast` player, Music Assistant 2.10 fronting the same speaker,
Cloud TTS) the day the repository was archived. Timings are relative to the
service call. None of these were fixed; they are recorded so that anyone
still installing the code knows what to expect. Issue numbers refer to this
repository unless noted.

### #2 — Volume never restored when the player is `off`

A Google speaker at rest is `off`. Core hides every media_player attribute
in that state (`homeassistant/components/media_player/__init__.py`,
`MediaPlayerEntity.state_attributes`, 2026.9.1 line 1142), so
`_current_volume()` reads `None`, `previous_volume` is `None` and the
restore is skipped: **the speaker stays at the announcement volume**.
Observed on every call from `off`: `off` → `idle` 0.40 → `playing` → `idle`
→ `off`, speaker left at 0.40 (was 0.30). The 0.2.0 diagnostics timeline
shows it plainly: `volume_before: null`, `volume_after_set: null`,
`volume_while_playing: 0.4`, no `volume_restored` step. A workable fix would
have been `media_player.turn_on` first (the Default Media Receiver loads,
the state becomes `idle` and `volume_level` is readable within ~2 s).

### #3 — The call blocks long after the clip has ended

The wait ends when the player's fingerprint (`state`, `media_content_id`,
`app_id`, `media_title`) is **equal** to the pre-announcement one. From
`off`, the player sits `idle` under the Default Media Receiver for ~10 s
after the clip before going back to `off`: a 4 s message costs 14–20 s.
With music playing before, the fingerprint never comes back and the call
waits the full `PLAYBACK_TIMEOUT` (30 s): measured 32–33 s. The README's
"up to 5 seconds" only covers the case where the media change is not
observed at all.

### #5 — The previous playback is never resumed

On Cast, `play_media` for the clip replaces whatever the receiver was
playing; nothing restarts it. Measured with a radio stream started by Home
Assistant (URL) and with a session started from a phone app (Radio France
app): both times the speaker ended `off`, silent. A best-effort resume
(re-issue `play_media` with the previous URL) would only ever cover media
Home Assistant itself started. The only paths that do resume: Music
Assistant's native announcement (measured: pause +0.5 s, voice +3.4 s,
volume restored +9.5 s, radio resumed +11–12 s) and Google's Broadcast
(`google_assistant_sdk`, needs a per-user Google Cloud project).

### #4 — A non-Cast player is accepted, and nothing says which path spoke

The `EntitySelector(integration="cast")` filter is applied by the frontend
only; an entry created through the API against a Music Assistant entity
fronting the same speaker was accepted. That entity advertises
`MEDIA_ANNOUNCE`, so the integration delegated to the native path and Music
Assistant applied its own announce-volume rule (+85 %: 0.30 → 0.55, 0.36 →
0.66) instead of the entry's `volume`. 0.1.1 emitted no debug line at all;
0.2.0's timeline helps but still does not name the path.

### #6 — Refusals over the REST API surface as HTTP 500

`ServiceValidationError` is not mapped to 4xx by
`homeassistant/components/api/__init__.py` (`APIDomainServicesView.post`
catches only `vol.Invalid` and `ServiceNotFound`), so a deny-list or
invalid-`data` refusal called through `POST /api/services/notify/...` gets
"500 Server got itself in trouble" plus an `aiohttp.server` traceback in
the log, on top of the integration's WARNING. Websocket and `blocking: true`
callers get the clean translated error. Core behaviour, documented here.

### #7 — README did not say what happens to the music

"Waits for the player to be doing what it was doing before restoring it"
reads as if playback resumed; it does not. See #5 for the alternatives.

### #8 — Scope: the core already does the job

`homeassistant/components/tts/notify.py` (legacy `notify: - platform: tts`,
still shipped in 2026.9.1, no deprecation issue in `notify/legacy.py`)
gives a `notify.<name>` on any `media_player`; Cloud TTS uses the
language's default voice (`cloud/tts.py`, `DEFAULT_VOICES`). Measured end
to end on a Music Assistant player: call returns in 0.05 s
(fire-and-forget), pause +0.5 s, voice +2.1 → +8.1 s, volume restored
+9.5 s, radio resumed +11.4 s. Its gaps versus this integration — no UI,
no error back to the caller, no per-call options, Core restart needed
(no `notify.reload` service) — were judged not worth a custom integration
once policy (deny list, quiet hours, priority) lives in a notify router.
This is the reason the repository was archived.

### What worked

Audio on a real Google Home Mini; the deny list (case-insensitive) and
`data` validation refusing without a sound; two near-simultaneous calls
serialized without overlap; volume restore when `volume_level` was visible
(player already playing); the 0.2.0 diagnostics timeline; service naming
from the entry title.

### AirPlay Notifier, same day

The sibling repository was archived for the same reason. Its Direct
strategy (`apple_tv`) can never resume a stream another device was sending;
its Music Assistant strategy already was the protocol-agnostic native path
(amiel-35/airplay-notifier#6, #7).
