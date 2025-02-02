#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Sebastian Andersson <sebastian@bittr.nu>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Moonraker agent to send Spoolman's spool info to Klipper

It listens for active_spool_set events from moonraker,
that will cause it to lookup the new spool's data
and for every field, if there exists a gcode macro
with the right name in Klipper, it will invoke it
with the field's value.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp
from jsonrpc_websocket import Server

MOONRAKER_URL = "ws://localhost:7125/websocket"
SPOOLMAN_URL = "http://localhost:7912/api"
KLIPPER_SPOOL_SET_MACRO_PREFIX = "_SPOOLMAN_SET_FIELD_"
KLIPPER_SPOOL_CLEAR_MACRO = "_SPOOLMAN_CLEAR_SPOOL"


# pylint: disable=R0903
class Spool2Klipper:
    """Moonraker agent to send Spoolman's spool info to Klipper"""

    def __init__(self):
        self.gcode_macros:List[str] = []
        self.spoolman_url = SPOOLMAN_URL
        self.http_session = None
        self.moonraker_server = None

    async def _fetch_spool_info(
        self, spool_id: Union[int, None]
    ) -> Optional[Union[Dict[str, Any], Exception]]:
        try:
            async with await self.http_session.get(
                f"{self.spoolman_url}/v1/spool/{spool_id}",
            ) as response:
                if response.status == 404:
                    return None
                if response.status == 200:
                    return await response.json()
                return Exception(await response.text())
        except aiohttp.client_exceptions.ClientConnectorError as e:
            return e

    async def _get_response_error(self, response: Exception) -> str:
        if isinstance(response, aiohttp.client_exceptions.ClientConnectorError):
            err_msg = f"Failed to connect to server: {response}"
        elif isinstance(response, Exception):
            err_msg = f"Unknown error: {response}"
        else:
            err_msg = f"Unknown error: {response}"
        return err_msg

    def _has_spoolman_set_macros(self) -> bool:
        prefix = KLIPPER_SPOOL_SET_MACRO_PREFIX
        for k in self.gcode_macros:
            if k.startswith(prefix):
                return True
        return False

    async def _notify_active_spool_set(self, params: Dict[str, Any]) -> None:
        spool_id = params["spool_id"]
        if spool_id is not None:
            if self._has_spoolman_set_macros():
                logging.debug("Fetching data from Spoolman id=%s", spool_id)
                spool_data = await self._fetch_spool_info(spool_id)
                if spool_data is None:
                    logging.info("Spool ID %s not found, clearing fields", spool_id)
                    await self._run_gcode(KLIPPER_SPOOL_CLEAR_MACRO)
                if isinstance(spool_data, Exception):
                    err_msg = self._get_response_error(spool_data)
                    logging.info("Attempt to fetch spool info failed: %s", err_msg)
                else:
                    spool_data: Dict[str, Any] = spool_data
                    logging.info("Fetched Spool data for ID %s", spool_id)
                    logging.debug("Got data from Spoolman: %s", spool_data)
                    await self._call_klipper_with_data(
                        KLIPPER_SPOOL_SET_MACRO_PREFIX,
                        spool_data,
                    )
            else:
                logging.debug("No spoolman gcode set macros found")
        else:
            if KLIPPER_SPOOL_CLEAR_MACRO in self.gcode_macros:
                await self._run_gcode(KLIPPER_SPOOL_CLEAR_MACRO)
            else:
                logging.debug("No spoolman gcode clear macro found")

    async def _call_klipper_with_data(
        self,
        prefix: str,
        spool_data: Any,
    ) -> None:

        for key, val in spool_data.items():
            macro_name = prefix + key
            if isinstance(val, dict):
                await self._call_klipper_with_data(macro_name + "_", val)
            elif macro_name in self.gcode_macros:
                if isinstance(val, int):
                    script = f"{macro_name} VALUE={val}"
                else:
                    val = val.replace('"', "''")
                    script = f'{macro_name} VALUE="{val}"'
                await self._run_gcode(script)

    async def _run_gcode(self, script):
        logging.info("Run in klipper: '%s'", script)
        await self.moonraker_server.printer.gcode.script(script=script, _notification=True)

    async def _routine(self):
        async with aiohttp.ClientSession() as self.http_session:
            self.moonraker_server = Server(MOONRAKER_URL)
            try:
                await self.moonraker_server.ws_connect()

                objects = await self.moonraker_server.printer.objects.list()
                self.gcode_macros = [
                    x[12:] for x in objects["objects"] if x.startswith("gcode_macro ")
                ]
                logging.debug("Available macros: %s", (self.gcode_macros))

                self.moonraker_server.notify_active_spool_set = self._notify_active_spool_set

                while True:
                    await asyncio.sleep(3600)
            finally:
                await self.moonraker_server.close()

    def run(self):
        """Run the agent in the async loop"""
        asyncio.get_event_loop().run_until_complete(self._routine())


if __name__ == "__main__":
    logging.basicConfig(encoding='utf-8', level=logging.DEBUG)
    spool2klipper = Spool2Klipper()
    spool2klipper.run()
