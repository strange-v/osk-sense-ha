"""Home Assistant config flow tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

try:
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.const import CONF_HOST
    from homeassistant.data_entry_flow import FlowResultType
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
    False,
    -71,
)
PULSE_BOOTSTRAP = GatewayBootstrap(BOOTSTRAP.info, NodeRegistry(2, (PULSE_NODE,)))


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
