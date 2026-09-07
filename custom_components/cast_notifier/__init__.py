"""The Cast Notifier integration.

Home Assistant has no `notify` that speaks: voice goes through `tts.speak`,
an action that `alert`, blueprints and automations cannot list as a
notifier. Cast Notifier fixes that with one `notify.*` service per
configured Cast player. See docs/ARCHITECTURE.md for the full contract.

This module wires a config entry to two independent notify surfaces defined
in notify.py:

- the legacy `notify.cast_<name>` service, loaded through the discovery
  helper so that `alert.notifiers:` can reference it by name;
- a modern `NotifyEntity`, set up as a regular entity platform.

Both surfaces share the same `CastSpeaker` instance stored on
`entry.runtime_data`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.notify.const import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.notify.legacy import (
    NOTIFY_SERVICES,
    BaseNotificationService,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery
from homeassistant.util import slugify
from homeassistant.util.hass_dict import HassKey

from .const import (
    CONF_ANNOUNCE_PREFIX,
    CONF_DENY_DOMAINS,
    CONF_LANGUAGE,
    CONF_MEDIA_PLAYER,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_RESTORE_VOLUME,
    CONF_SERVICE_NAME,
    CONF_SERVICE_NAME_BASE,
    CONF_TTS_ENTITY,
    CONF_VOICE,
    CONF_VOLUME,
    DEFAULT_DENY_DOMAINS,
    DEFAULT_RESTORE_VOLUME,
    DOMAIN,
)
from .speaker import CastSpeaker, CastSpeakerConfig

PLATFORMS: list[Platform] = [Platform.NOTIFY]

# Which entry owns which `notify.*` service name, for as long as that entry
# is set up. The name a *stopped* entry owns lives in its `entry.data`
# instead (`CONF_SERVICE_NAME`); this map is what keeps two entries setting
# up in the same event loop iteration from claiming one name, and what the
# unload callback checks before removing a service it may no longer own.
SERVICE_OWNERS: HassKey[dict[str, str]] = HassKey(f"{DOMAIN}_service_owners")


@dataclass(slots=True)
class CastNotifierRuntimeData:
    """Runtime data stored on the config entry."""

    speaker: CastSpeaker
    service_name: str
    # Set by notify.py's `async_get_service` once the legacy service has
    # actually been created, so unload knows which instance to retract.
    legacy_service: BaseNotificationService | None = field(default=None)
    # Set by the unload callback. The legacy platform is registered from a
    # dispatcher callback that Home Assistant runs in a task of its own
    # (`helpers/discovery.py` -> `async_dispatcher_send_internal`), which
    # nothing here owns: it can still be in flight when the entry unloads,
    # and would then re-register a service that was just retracted. See
    # `async_get_service`.
    unloaded: bool = field(default=False)


type CastNotifierConfigEntry = ConfigEntry[CastNotifierRuntimeData]


def _base_service_name(entry: CastNotifierConfigEntry) -> str:
    """Derive `cast_<name>` from a config entry's title.

    The title is what the user sees and can rename, which the
    `media_player` entity id is not: a player that Home Assistant had to
    auto-suffix (`media_player.kitchen_2`) used to produce
    `notify.cast_kitchen_2` for an entry titled "Kitchen", a name nothing
    in the UI could have predicted.

    `notify/legacy.py::async_setup_legacy.async_setup_platform` slugifies
    whatever is passed as `CONF_NAME` in the discovery payload into the
    final service name, so slugifying here is belt and braces -- but it is
    also what lets this module know the name it will get, which is what
    the unload callback needs to retract it.

    A title with nothing sluggable in it (`"!!!"`) would give
    `notify.cast_`, which no automation can call, so the player's object
    id is the fallback.
    """
    slug = slugify(entry.title)
    if not slug:
        slug = slugify(entry.data[CONF_MEDIA_PLAYER].split(".", 1)[-1])
    return f"cast_{slug}"


@callback
def _async_service_name(hass: HomeAssistant, entry: CastNotifierConfigEntry) -> str:
    """Return the `notify.*` service name this entry owns, freezing it.

    Two players can legitimately be called the same thing, and a service
    name has to be unique, so a duplicate slug is suffixed `_2`, `_3`, ...
    Which entry gets which suffix is decided **once**, the first time the
    entry is set up, and written to `entry.data`: from then on the name is
    reused verbatim, and only a change of the entry title (i.e. of the
    slug the name is derived from) can move it.

    That is the whole point. A name recomputed from the entry list on
    every setup moves under an entry that did not change -- deleting the
    first of two entries titled "Speaker" used to promote the second from
    `cast_speaker_2` to `cast_speaker` -- and, worse, can land on a name
    another entry already registered:
    `notify/legacy.py::BaseNotificationService.async_register_services`
    returns early when `hass.services.has_service(DOMAIN,
    self._service_name)` is already true (2026.9.1, line 312), so the
    second claimant registers nothing at all while believing it owns the
    service, and retracting it on unload would silence the first.

    A frozen name also makes the assignment independent of setup order,
    which matters because Home Assistant sets a domain's entries up
    concurrently (`homeassistant/setup.py`, `asyncio.gather` over
    `entry.async_setup_locked`).

    This is a `callback`: the ownership claim and the `entry.data` write
    happen without an await between them, so two entries setting up in
    the same event loop iteration cannot both claim one name.
    """
    base = _base_service_name(entry)
    owners = hass.data.setdefault(SERVICE_OWNERS, {})
    stored = entry.data.get(CONF_SERVICE_NAME)
    if (
        isinstance(stored, str)
        and stored
        and entry.data.get(CONF_SERVICE_NAME_BASE) == base
        and stored not in _taken_service_names(hass, entry)
    ):
        owners[stored] = entry.entry_id
        return stored

    name = _free_service_name(hass, entry, base)
    owners[name] = entry.entry_id
    # An entry Home Assistant does not know about (a bare `MockConfigEntry`
    # in a unit test) cannot be updated, and has nothing to persist for.
    if hass.config_entries.async_get_entry(entry.entry_id) is not None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_SERVICE_NAME: name,
                CONF_SERVICE_NAME_BASE: base,
            },
        )
    return name


def _taken_service_names(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> set[str]:
    """Return the names other entries of this integration have claimed.

    Both halves matter: `entry.data` covers entries that are not loaded
    (disabled, failed, or simply not set up yet) and would otherwise look
    free, and `hass.data[SERVICE_OWNERS]` covers a loaded entry whose name
    was claimed in this same event loop iteration, before its write to
    `entry.data` could be seen.
    """
    taken = {
        name
        for other in hass.config_entries.async_entries(DOMAIN, include_ignore=False)
        if other.entry_id != entry.entry_id
        and isinstance(name := other.data.get(CONF_SERVICE_NAME), str)
    }
    taken.update(
        name
        for name, owner in hass.data.get(SERVICE_OWNERS, {}).items()
        if owner != entry.entry_id
    )
    return taken


def _free_service_name(
    hass: HomeAssistant, entry: CastNotifierConfigEntry, base: str
) -> str:
    """Pick a name for an entry that does not have a usable one yet.

    The starting point is the entry's rank, in creation order, among the
    entries that want the same base and have not frozen a name yet
    (`hass.config_entries.async_entries` is insertion-ordered, backed by
    `ConfigEntryItems` in `homeassistant/config_entries.py`). That is what
    makes an upgrade from 0.1.x -- several entries at once, none of them
    with a stored name -- come out the same whichever of them Home
    Assistant happens to set up first, and it counts entries that are
    disabled, so enabling or disabling one does not renumber its
    neighbours. Ignored entries never set up and never own a service, so
    they are skipped.

    Names another entry has claimed are then skipped, and so are names a
    *different integration* already serves: core would refuse to register
    over one of those (see `_async_service_name`), leaving this entry mute
    and its unload deleting somebody else's service.
    """
    taken = _taken_service_names(hass, entry)
    rank = 1
    for other in hass.config_entries.async_entries(DOMAIN, include_ignore=False):
        if other.entry_id == entry.entry_id:
            break
        if CONF_SERVICE_NAME not in other.data and _base_service_name(other) == base:
            rank += 1

    name = base if rank == 1 else f"{base}_{rank}"
    while name in taken or _is_foreign_service(hass, name):
        rank += 1
        name = f"{base}_{rank}"
    return name


def _is_foreign_service(hass: HomeAssistant, name: str) -> bool:
    """Return whether `notify.<name>` is served by something else."""
    return hass.services.has_service(NOTIFY_DOMAIN, name) and name not in hass.data.get(
        SERVICE_OWNERS, {}
    )


def _build_speaker_config(entry: CastNotifierConfigEntry) -> CastSpeakerConfig:
    """Build the speaker config from a config entry's data and options."""
    options = entry.options
    return CastSpeakerConfig(
        media_player=entry.data[CONF_MEDIA_PLAYER],
        tts_entity=options[CONF_TTS_ENTITY],
        language=options.get(CONF_LANGUAGE),
        voice=options.get(CONF_VOICE),
        volume=options.get(CONF_VOLUME),
        restore_volume=options.get(CONF_RESTORE_VOLUME, DEFAULT_RESTORE_VOLUME),
        announce_prefix=options.get(CONF_ANNOUNCE_PREFIX),
        deny_domains=[
            domain.casefold()
            for domain in options.get(CONF_DENY_DOMAINS, DEFAULT_DENY_DOMAINS)
        ],
        quiet_start=options.get(CONF_QUIET_START),
        quiet_end=options.get(CONF_QUIET_END),
        quiet_volume=options.get(CONF_QUIET_VOLUME),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Set up Cast Notifier from a config entry."""
    speaker = CastSpeaker(hass, _build_speaker_config(entry))
    service_name = _async_service_name(hass, entry)
    runtime_data = CastNotifierRuntimeData(speaker=speaker, service_name=service_name)
    entry.runtime_data = runtime_data

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    @callback
    def _async_retract_legacy_service() -> None:
        """Remove the legacy `notify.cast_<name>` service on unload.

        Home Assistant never does this for us: `notify/legacy.py` only
        unregisters legacy services from `async_reset_platform`, which is
        called from `homeassistant/helpers/reload.py` for YAML reloads --
        never from a config entry unload. Without this, deleting an entry
        leaves a dangling `notify.cast_<name>` that calls into a dead
        speaker, and re-adding or reconfiguring it is a no-op because
        `BaseNotificationService.async_register_services` returns early
        when `hass.services.has_service(...)` is already true.

        `hass.services.async_remove` rather than the service object's own
        `async_unregister_services()`: that method is a coroutine, and an
        `entry.async_on_unload` callback is synchronous, so calling it
        would mean firing a task whose completion nothing waits for -- the
        entry could be set up again before the old service is gone. It
        also reads `self._service_name`, a private attribute that only
        exists once `async_register_services` has run, which is not
        guaranteed here (discovery may never have completed). Removing the
        service by name is synchronous, and correct either way.
        """
        runtime_data.unloaded = True
        # Only ever the service this entry owns. Removing `notify.<name>`
        # by name alone would let one entry retract another's service --
        # and core, which never registered a second service under that
        # name, would leave the other entry LOADED and mute.
        owners = hass.data.get(SERVICE_OWNERS, {})
        if owners.get(service_name) == entry.entry_id:
            del owners[service_name]
            if hass.services.has_service(NOTIFY_DOMAIN, service_name):
                hass.services.async_remove(NOTIFY_DOMAIN, service_name)
        services = hass.data.get(NOTIFY_SERVICES, {}).get(DOMAIN)
        instance = runtime_data.legacy_service
        if services is not None and instance is not None and instance in services:
            services.remove(instance)
            if not services:
                del hass.data[NOTIFY_SERVICES][DOMAIN]
        runtime_data.legacy_service = None

    entry.async_on_unload(_async_retract_legacy_service)

    # Legacy `notify.cast_<name>` service, discovered like `mobile_app` does
    # for its own per-device services. `CONF_NAME` is what the legacy notify
    # platform slugifies into the service name (see notify/legacy.py:
    # async_setup_legacy.async_setup_platform).
    # Tied to the entry rather than to `hass`: `ConfigEntry.async_unload`
    # awaits the entry's own tasks (`_async_process_on_unload` in
    # `config_entries.py`), so the discovery cannot still be running
    # against an entry that no longer exists. A `hass.async_create_task`
    # is owned by nobody and outlives the entry entirely.
    entry.async_create_task(
        hass,
        discovery.async_load_platform(
            hass,
            Platform.NOTIFY,
            DOMAIN,
            {CONF_NAME: service_name, "entry_id": entry.entry_id},
            {},
        ),
        name="cast_notifier legacy notify discovery",
        eager_start=True,
    )

    # Modern `NotifyEntity`.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Unload a config entry.

    The legacy service is retracted by the `entry.async_on_unload` callback
    registered in `async_setup_entry`, which Home Assistant runs once this
    returns `True`.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> bool:
    """Migrate an old config entry.

    Nothing to do yet: the entry format has not changed since the first
    release (`VERSION` 1, `MINOR_VERSION` 1). This exists so that the first
    schema change ships as a migration instead of as a broken entry, and so
    the shape of that future migration is already decided.
    """
    return True


async def _async_update_options(
    hass: HomeAssistant, entry: CastNotifierConfigEntry
) -> None:
    """Reload the entry when its options -- or its title -- change.

    The reload is what makes an options change take effect on the legacy
    service too: unloading retracts `notify.cast_<name>`, so setting up
    again registers it afresh against a `CastSpeaker` built from the new
    options (see `_async_retract_legacy_service`).

    A rename goes through the same path: `async_update_entry` fires the
    update listeners for a changed `title` exactly as it does for changed
    `options` (`homeassistant/config_entries.py`, `_async_update_entry` ->
    `_async_save_and_notify`), so renaming the entry in the UI retracts
    `notify.cast_<old title>` and registers `notify.cast_<new title>`.
    """
    await hass.config_entries.async_reload(entry.entry_id)
