"""Home Assistant config flow tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

try:
    from homeassistant.config_entries import (
        SOURCE_IGNORE,
        SOURCE_REAUTH,
        SOURCE_USER,
        SOURCE_ZEROCONF,
        ConfigEntryState,
    )
    from homeassistant.const import CONF_HOST
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ModuleNotFoundError as error:
    raise unittest.SkipTest("requires a Home Assistant test environment") from error

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.osk_sense.api import (
    ApiResponseError,
    AuthenticationError,
    CannotConnectError,
    GatewayBootstrap,
    GatewayInfo,
    GatewayUiInfo,
    InvalidResponseError,
    NodeInfo,
    NodeRegistry,
    UnsupportedVersionError,
)
from custom_components.osk_sense.const import (
    CONF_DEVICE_CLASS,
    CONF_PULSE_COUNTERS,
    CONF_TOKEN,
    CONF_UNIT,
    CONF_UNITS_PER_PULSE,
    DOMAIN,
)
from custom_components.osk_sense.runtime import GatewayRuntime

BOOTSTRAP = GatewayBootstrap(
    info=GatewayInfo(
        firmware_version="0.8.0",
        api_version=1,
        stream_version=1,
        gateway_id="cccd7e8a5e2bd5d8b9cb754240a82fd8",
        boot_id="1d52f8108f3098bdcc0e1ac5fc71be4b",
        ui=GatewayUiInfo("ready", "0.1.0", "0.8"),
        board="Waveshare ESP32-S3-ETH + PoE",
        hostname="osk-hub-test",
        uptime_seconds=12345,
    ),
    registry=NodeRegistry(1, ()),
)

PULSE_NODE = NodeInfo(
    7,
    "102132435465768798A9",
    "Water meter",
    6,
    "1.3.0",
    "active",
    True,
    None,
    -70,
    2,
    "auto",
    None,
    2,
    2,
    False,
    -71,
)
PULSE_BOOTSTRAP = GatewayBootstrap(BOOTSTRAP.info, NodeRegistry(2, (PULSE_NODE,)))


def _discovery(
    gateway_id: str = BOOTSTRAP.info.gateway_id,
    *,
    address: str = "192.0.2.15",
    hostname: str = "osk-hub-test",
) -> ZeroconfServiceInfo:
    ip = ip_address(address)
    return ZeroconfServiceInfo(
        ip_address=ip,
        ip_addresses=[ip],
        port=80,
        hostname=f"{hostname}.local.",
        type="_osk-sense._tcp.local.",
        name=f"{hostname}._osk-sense._tcp.local.",
        properties={"gateway_id": gateway_id},
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_updates_existing_address_and_preserves_entry(hass) -> None:
    """A moved gateway keeps its entry and credentials."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://192.0.2.10", CONF_TOKEN: "old-token"},
        options={"pulse_counters": {"node": {"units_per_pulse": 2}}},
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.osk_sense.config_flow.GatewayApiClient"
        ) as client_class,
        patch.object(hass.config_entries, "async_schedule_reload") as reload_entry,
    ):
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_discovery(),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: "http://192.0.2.15", CONF_TOKEN: "old-token"}
    assert entry.options == {"pulse_counters": {"node": {"units_per_pulse": 2}}}
    assert entry.unique_id == BOOTSTRAP.info.gateway_id
    client_class.assert_called_once()
    assert client_class.call_args.args[:2] == ("http://192.0.2.15", None)
    client.async_get_info.assert_awaited_once()
    reload_entry.assert_called_once_with(entry.entry_id)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_keeps_address_when_unchanged(hass) -> None:
    """Repeated advertisements do not schedule another connection."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://192.0.2.15", CONF_TOKEN: "secret"},
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.osk_sense.config_flow.GatewayApiClient"
        ) as client_class,
        patch.object(hass.config_entries, "async_schedule_reload") as reload_entry,
    ):
        client_class.return_value.base_url = "http://192.0.2.15"
        client_class.return_value.async_get_info = AsyncMock(
            return_value=BOOTSTRAP.info
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_HOST] == "http://192.0.2.15"
    reload_entry.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_rejects_wrong_identity(hass) -> None:
    """A TXT record that disagrees with the API cannot change an entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://192.0.2.10", CONF_TOKEN: "secret"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_get_info = AsyncMock(
            return_value=BOOTSTRAP.info
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_discovery("f" * 32),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert entry.data[CONF_HOST] == "http://192.0.2.10"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_does_not_change_ignored_entry(hass) -> None:
    """An ignored gateway stays ignored without stored connection details."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id=BOOTSTRAP.info.gateway_id,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_get_info = AsyncMock(
            return_value=BOOTSTRAP.info
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_collects_token_for_new_gateway(hass) -> None:
    """A new gateway needs a token before its config entry is created."""
    with (
        patch(
            "custom_components.osk_sense.config_flow.GatewayApiClient"
        ) as client_class,
        patch.object(hass.config_entries, "async_setup", AsyncMock(return_value=True)),
    ):
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        client.async_bootstrap = AsyncMock(return_value=BOOTSTRAP)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "zeroconf_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == BOOTSTRAP.info.gateway_id
    assert result["data"] == {CONF_HOST: "http://192.0.2.15", CONF_TOKEN: "secret"}
    client.async_bootstrap.assert_awaited_once_with(
        expected_gateway_id=BOOTSTRAP.info.gateway_id
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_replaces_pending_flow_after_hostname_change(hass) -> None:
    """A renamed gateway replaces its pending card instead of adding another."""
    renamed = replace(BOOTSTRAP.info, hostname="osk-hub")
    with (
        patch(
            "custom_components.osk_sense.config_flow.GatewayApiClient"
        ) as client_class,
        patch.object(hass.config_entries, "async_setup", AsyncMock(return_value=True)),
    ):
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        assert first["type"] is FlowResultType.FORM

        client.base_url = "http://192.0.2.16"
        client.async_get_info = AsyncMock(return_value=renamed)
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_discovery(address="192.0.2.16", hostname="osk-hub"),
        )
        assert second["type"] is FlowResultType.FORM

        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [flow["flow_id"] for flow in flows] == [second["flow_id"]]
        assert flows[0]["context"]["title_placeholders"]["name"] == "osk-hub"

        client.async_bootstrap = AsyncMock(
            return_value=replace(BOOTSTRAP, info=renamed)
        )
        result = await hass.config_entries.flow.async_configure(
            second["flow_id"], {CONF_TOKEN: "secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "osk-hub"
    assert result["data"] == {CONF_HOST: "http://192.0.2.16", CONF_TOKEN: "secret"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_replaces_pending_flow_for_new_gateway_id(hass) -> None:
    """A new gateway ID at the same address replaces the unusable old card."""
    other_gateway = "f" * 32
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        assert first["type"] is FlowResultType.FORM

        client.async_get_info = AsyncMock(
            return_value=replace(BOOTSTRAP.info, gateway_id=other_gateway)
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery(other_gateway)
        )

    assert second["type"] is FlowResultType.FORM
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["flow_id"] for flow in flows] == [second["flow_id"]]
    assert flows[0]["context"]["unique_id"] == other_gateway


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_keeps_pending_flow_for_other_gateway(hass) -> None:
    """Different gateways at different addresses keep separate cards."""
    other_gateway = "f" * 32
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        client.base_url = "http://192.0.2.16"
        client.async_get_info = AsyncMock(
            return_value=replace(
                BOOTSTRAP.info, gateway_id=other_gateway, hostname="osk-hub-2"
            )
        )
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_discovery(other_gateway, address="192.0.2.16", hostname="osk-hub-2"),
        )

    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_keeps_pending_flow_when_unchanged(hass) -> None:
    """Repeated announcements do not reset a card the user may be filling in."""
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["flow_id"] for flow in flows] == [first["flow_id"]]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_rechecks_identity_before_creating_entry(hass) -> None:
    """An address reused while the token form is open cannot create an entry."""
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client = client_class.return_value
        client.base_url = "http://192.0.2.15"
        client.async_get_info = AsyncMock(return_value=BOOTSTRAP.info)
        client.async_bootstrap = AsyncMock(
            return_value=replace(
                BOOTSTRAP, info=replace(BOOTSTRAP.info, gateway_id="f" * 32)
            )
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_discovery()
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "secret"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_gateway"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_creates_unique_gateway_entry(hass) -> None:
    """Test the complete manual setup flow."""
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client = client_class.return_value
        client.base_url = "http://osk-hub.local"
        client.async_bootstrap = AsyncMock(return_value=BOOTSTRAP)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "osk-hub.local", CONF_TOKEN: "secret"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "osk-hub-test"
    assert result["data"] == {
        CONF_HOST: "http://osk-hub.local",
        CONF_TOKEN: "secret",
    }
    assert result["result"].unique_id == BOOTSTRAP.info.gateway_id


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ValueError(), "invalid_host"),
        (CannotConnectError(), "cannot_connect"),
        (AuthenticationError(), "invalid_auth"),
        (InvalidResponseError(), "invalid_response"),
        (ApiResponseError(500), "invalid_response"),
        (UnsupportedVersionError(2, 1), "unsupported_version"),
        (RuntimeError(), "unknown"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_reports_client_errors(hass, error, reason) -> None:
    """Test recoverable errors remain on the form."""
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_bootstrap = AsyncMock(side_effect=error)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "osk-hub.local", CONF_TOKEN: "secret"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_user_flow_rejects_duplicate_gateway(hass) -> None:
    """Test a gateway can have only one config entry."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=BOOTSTRAP.info.gateway_id)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_bootstrap = AsyncMock(return_value=BOOTSTRAP)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "osk-hub.local", CONF_TOKEN: "secret"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_updates_token_and_reloads_entry(hass) -> None:
    """Test a valid replacement token is stored for the same gateway."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://osk-hub.local", CONF_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.osk_sense.config_flow.GatewayApiClient"
        ) as client_class,
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(return_value=True),
        ) as reload_entry,
    ):
        client_class.return_value.async_bootstrap = AsyncMock(return_value=BOOTSTRAP)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "new-token"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {
        CONF_HOST: "http://osk-hub.local",
        CONF_TOKEN: "new-token",
    }
    assert client_class.call_args.args[:2] == (
        "http://osk-hub.local",
        "new-token",
    )
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ValueError(), "invalid_auth"),
        (AuthenticationError(), "invalid_auth"),
        (CannotConnectError(), "cannot_connect"),
        (InvalidResponseError(), "invalid_response"),
        (ApiResponseError(500), "invalid_response"),
        (UnsupportedVersionError(2, 1), "unsupported_version"),
        (RuntimeError(), "unknown"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_reports_client_errors(hass, error, reason) -> None:
    """Test reauthentication errors stay on the token form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://osk-hub.local", CONF_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_bootstrap = AsyncMock(side_effect=error)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "new-token"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": reason}
    assert entry.data[CONF_TOKEN] == "old-token"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_rejects_token_for_another_gateway(hass) -> None:
    """Test a token cannot silently move an entry to another gateway."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=BOOTSTRAP.info.gateway_id,
        data={CONF_HOST: "http://osk-hub.local", CONF_TOKEN: "old-token"},
    )
    entry.add_to_hass(hass)
    other_bootstrap = replace(
        BOOTSTRAP,
        info=replace(BOOTSTRAP.info, gateway_id="f" * 32),
    )
    with patch(
        "custom_components.osk_sense.config_flow.GatewayApiClient"
    ) as client_class:
        client_class.return_value.async_bootstrap = AsyncMock(
            return_value=other_bootstrap
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "other-token"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_gateway"
    assert entry.data[CONF_TOKEN] == "old-token"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_configures_nonmetric_pulse_total(hass) -> None:
    """Test configuring a converted water total in US gallons."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = GatewayRuntime(AsyncMock(), PULSE_BOOTSTRAP)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device_uid": PULSE_NODE.device_uid}
    )
    assert result["step_id"] == "counter"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UNITS_PER_PULSE: 0.01,
            CONF_DEVICE_CLASS: "water",
            CONF_UNIT: "gal",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PULSE_COUNTERS][PULSE_NODE.device_uid] == {
        CONF_UNITS_PER_PULSE: 0.01,
        CONF_DEVICE_CLASS: "water",
        CONF_UNIT: "gal",
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_rejects_incompatible_unit(hass) -> None:
    """Test that a volume unit cannot be assigned to an energy sensor."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = GatewayRuntime(AsyncMock(), PULSE_BOOTSTRAP)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device_uid": PULSE_NODE.device_uid}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UNITS_PER_PULSE: 1,
            CONF_DEVICE_CLASS: "energy",
            CONF_UNIT: "gal",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_UNIT: "incompatible_unit"}
