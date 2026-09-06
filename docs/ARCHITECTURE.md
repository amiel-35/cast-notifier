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

`CastSpeaker.async_speak`, for a call carrying a volume (from the config
entry's `volume` option or a per-call `data.volume` override):

1. Reads the player's current `volume_level` (`ATTR_MEDIA_VOLUME_LEVEL`,
   `homeassistant/components/media_player/const.py`) and remembers it.
2. Calls `media_player.volume_set` (`SERVICE_VOLUME_SET`,
   `homeassistant/const.py`) to the requested volume.
3. Records the player's current `state`.
4. Calls `tts.speak` as above.
5. Waits for the player to leave that state and come back to it --
   `homeassistant/helpers/event.py::async_track_state_change_event` -- capped
   at `PLAYBACK_TIMEOUT` (30s) in total so a player that never truly
   settles back (e.g. it kept playing something unrelated) cannot block a
   caller forever.
6. If `restore_volume` (default `True`), restores the volume from step 1.

If no volume is configured for the call, none of this runs: Cast Notifier
just calls `tts.speak` and returns, exactly like calling the action
directly, only reachable as a `notify.*` service.

`CastSpeaker` also checks `MediaPlayerEntityFeature.MEDIA_ANNOUNCE` on the
target (`ATTR_SUPPORTED_FEATURES` in the entity's state attributes) and
skips its own volume/wait dance when the target declares that support: such
an entity is expected to duck and wait for the announcement inside its own
`async_play_media`, in which case managing volume manually would fight the
platform instead of helping it. Against a real Cast player this branch
never triggers today (see above); it exists so that this integration, or a
future Cast release, benefits automatically once/if that changes.

## Input contract

`notify.cast_<name>`, and the `NotifyEntity`'s `send_message`, accept:

- `message`: the text to speak (required).
- `data.tts_entity`, `data.language`, `data.voice`, `data.volume`: per-call
  overrides of the matching config entry option.
- `data.source_entity`: the entity a message is *about*
  (e.g. `alarm_control_panel.home`). Never spoken; used only to enforce
  `deny_domains`.

The `NotifyEntity` surface has no `data` payload (`NotifyEntityFeature`
does not define one), so per-call overrides and `deny_domains` enforcement
are only reachable through the legacy service today.

## The security rule: `deny_domains`

`deny_domains` defaults to `alarm_control_panel` and `lock`. When
`data.source_entity`'s domain is in that list, the message is refused
(logged as a warning, `CastNotifierRefused` raised internally) before it
ever reaches `tts.speak`. This is a deliberate, hardcoded-by-default
safety rule: alarm and lock state should never be inferable from what a
speaker says out loud, even if someone accidentally wires an automation
that way. `deny_domains` is configurable (options flow) so a deployment can
extend it, but the two defaults are the whole reason this exists.

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

## Config entry model

One config entry = one Cast player. `entry.data` holds only
`media_player` (the entry's identity: `async_set_unique_id(media_player)` +
`_abort_if_unique_id_configured()` prevent configuring the same player
twice). Everything else -- `tts_entity`, `language`, `voice`, `volume`,
`restore_volume`, `announce_prefix`, `deny_domains` -- lives in
`entry.options` and is editable through the options flow without deleting
and re-adding the entry. Changing options reloads the entry (like
`notify-switchboard` does), which re-registers the legacy service and
rebuilds the `CastSpeaker` with the new settings.

## What this is not

Cast Notifier has no dependency on, and no awareness of, any other
notification project (e.g. a per-person routing layer). It is a single,
independent `notify.*` per Cast player -- nothing more.
