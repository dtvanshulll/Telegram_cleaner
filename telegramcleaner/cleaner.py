from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from telebridge import TeleBridgeApp
from tqdm import tqdm

from .config import CleanerConfig

DEFAULT_BATCH_SIZE = 100
PROGRESS_UPDATE_INTERVAL = 3.0
LOGGER = logging.getLogger("telegramcleaner")


@dataclass(slots=True)
class ChannelCleanupResult:
    channel: str
    total_messages: int
    deleted_messages: int
    failed_messages: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AvailableChannel:
    reference: Any
    label: str


class TelegramCleaner:
    """Delete Telegram messages directly or through Telegram-side userbot commands."""

    COMMAND_ALIASES = {
        "da": "deleteall",
        "d": "delete",
        "c": "clean",
        "s": "status",
        "h": "help",
        "x": "stop",
        "p": "pause",
        "r": "resume",
    }

    def __init__(
        self,
        config: CleanerConfig,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        log_level: str = "WARNING",
    ) -> None:
        self.config = config
        self.batch_size = batch_size
        self.app = TeleBridgeApp().setup(
            api_id=config.api_id,
            api_hash=config.api_hash,
            session_name=config.session_name,
            auto_load_plugins=False,
            log_level=log_level.upper(),
        )
        self._user_client: Any | None = None
        self._self_user_id: int | None = None
        self._started = False
        self._command_listener_attached = False
        self._cleanup_task: asyncio.Task[ChannelCleanupResult | None] | None = None
        self._state_lock = asyncio.Lock()
        self._stop_requested = False
        self._progress_message_chat_id: int | None = None
        self._progress_message_id: int | None = None
        self._progress_target_chat_id: int | None = None
        self._last_progress_text = ""
        self._last_progress_publish = 0.0
        self._protected_message_ids: dict[int, set[int]] = {}
        self.state: dict[str, bool | str | int] = {
            "running": False,
            "paused": False,
            "current_channel": "",
            "deleted": 0,
            "failed": 0,
            "total": 0,
        }

    async def start(self) -> None:
        self.app.validate_startup()
        await self.app.client.start(self.app.router)

        if self.app.client.user_client is None:
            raise RuntimeError("TeleBridge did not initialize a Telegram userbot client.")

        self._user_client = self.app.client.user_client
        me = await self._client.get_me()
        self._self_user_id = getattr(me, "id", None)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return

        await self.request_stop()
        if self._cleanup_task is not None:
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
            self._cleanup_task = None

        try:
            await self.app.client.stop()
        finally:
            self._started = False

    async def run(self) -> list[ChannelCleanupResult]:
        await self.start()
        try:
            return await self.clean_channels()
        finally:
            await self.stop()

    async def run_command_mode(self) -> None:
        await self.start()
        await self.listen_channel_commands()
        tqdm.write(
            "Command mode is active. Use deleteall/da, delete/d, clean/c, status/s, help/h, pause/p, resume/r, stop/x."
        )
        try:
            await self._client.run_until_disconnected()
        finally:
            await self.stop()

    async def clean_channels(self) -> list[ChannelCleanupResult]:
        results: list[ChannelCleanupResult] = []
        for channel_reference in self.config.channels:
            tqdm.write(f"\nCleaning channel: {channel_reference}")
            try:
                result = await self.clean_channel(channel_reference)
            except Exception as error:
                LOGGER.exception("Cleanup failed for %s: %s", channel_reference, error)
                tqdm.write(f"Failed to clean {channel_reference}: {error}")
                result = ChannelCleanupResult(
                    channel=channel_reference,
                    total_messages=0,
                    deleted_messages=0,
                    failed_messages=0,
                    error=str(error),
                )
            results.append(result)
        return results

    async def clean_channel(self, channel_reference: Any) -> ChannelCleanupResult:
        entity, channel_label, _ = await self._resolve_entity_target(channel_reference)
        return await self._run_tracked_cleanup(
            channel_label,
            lambda: self._clean_all_messages(entity, channel_label),
        )

    async def clean_current_chat(self, event: Any) -> ChannelCleanupResult:
        entity, channel_label, chat_id = await self._resolve_event_target(event)
        return await self._run_tracked_cleanup(
            channel_label,
            lambda: self._clean_all_messages(entity, channel_label, target_chat_id=chat_id),
        )

    async def clean_last_n(self, chat: Any, n: int) -> ChannelCleanupResult:
        if n <= 0:
            raise ValueError("Message count must be greater than zero.")

        entity, channel_label, chat_id = await self._resolve_chat_target(chat)
        return await self._run_tracked_cleanup(
            channel_label,
            lambda: self._clean_recent_messages(entity, channel_label, n, target_chat_id=chat_id),
        )

    async def clean_specific_channel(self, channel: str) -> ChannelCleanupResult:
        return await self.clean_channel(channel)

    async def listen_channel_commands(self) -> None:
        if self._command_listener_attached:
            return

        from telethon import events

        self._client.add_event_handler(self._handle_command_event, events.NewMessage(outgoing=True))
        self._command_listener_attached = True

    async def request_pause(self) -> bool:
        async with self._state_lock:
            if not bool(self.state["running"]) or bool(self.state["paused"]):
                return False
            self.state["paused"] = True
            return True

    async def request_resume(self) -> bool:
        async with self._state_lock:
            if not bool(self.state["running"]) or not bool(self.state["paused"]):
                return False
            self.state["paused"] = False
            return True

    async def request_stop(self) -> bool:
        async with self._state_lock:
            if not bool(self.state["running"]):
                return False
            self._stop_requested = True
            self.state["running"] = False
            self.state["paused"] = False
            return True

    async def _handle_command_event(self, event: Any) -> None:
        if not getattr(event, "out", False):
            return

        if self._self_user_id is None:
            me = await self._client.get_me()
            self._self_user_id = getattr(me, "id", None)

        if getattr(event, "sender_id", None) != self._self_user_id:
            return

        raw_text = (getattr(event, "raw_text", None) or "").strip()
        if not raw_text:
            return

        parsed = self._parse_command(raw_text)
        if parsed is None:
            return

        command, argument = parsed

        if command == "status":
            await self._reply_to_event(event, await self._format_status_text(header="Current status"))
            return

        if command == "help":
            await self._reply_to_event(event, self._help_text())
            return

        if command == "pause":
            paused = await self.request_pause()
            message = "Cleanup paused." if paused else "No active cleanup to pause."
            await self._reply_to_event(event, message)
            return

        if command == "resume":
            resumed = await self.request_resume()
            message = "Cleanup resumed." if resumed else "No paused cleanup to resume."
            await self._reply_to_event(event, message)
            return

        if command == "stop":
            stopped = await self.request_stop()
            message = (
                "Stop requested. The current cleanup will halt after the active step."
                if stopped
                else "No active cleanup to stop."
            )
            await self._reply_to_event(event, message)
            return

        try:
            if command == "deleteall":
                entity, channel_label, chat_id = await self._resolve_event_target(event)
                await self._start_command_cleanup(
                    control_event=event,
                    channel_label=channel_label,
                    target_chat_id=chat_id,
                    cleanup_factory=lambda: self._clean_all_messages(
                        entity,
                        channel_label,
                        target_chat_id=chat_id,
                    ),
                )
                return

            if command == "delete":
                if argument is None:
                    await self._reply_to_event(event, "Usage: delete <number> or d <number>")
                    return

                try:
                    count = int(argument)
                except ValueError:
                    await self._reply_to_event(event, f"Invalid number: {argument}")
                    return

                if count <= 0:
                    await self._reply_to_event(event, "Message count must be greater than zero.")
                    return

                entity, channel_label, chat_id = await self._resolve_event_target(event)
                await self._start_command_cleanup(
                    control_event=event,
                    channel_label=channel_label,
                    target_chat_id=chat_id,
                    cleanup_factory=lambda: self._clean_recent_messages(
                        entity,
                        channel_label,
                        count,
                        target_chat_id=chat_id,
                    ),
                )
                return

            if command == "clean":
                if argument is None:
                    await self._reply_to_event(event, "Usage: clean <channel> or c <channel>")
                    return

                entity, channel_label, _ = await self._resolve_entity_target(argument)
                await self._start_command_cleanup(
                    control_event=event,
                    channel_label=channel_label,
                    target_chat_id=None,
                    cleanup_factory=lambda: self._clean_all_messages(entity, channel_label),
                )
        except Exception as error:
            await self._reply_to_event(event, f"Unable to start cleanup: {error}")

    async def _start_command_cleanup(
        self,
        *,
        control_event: Any,
        channel_label: str,
        target_chat_id: int | None,
        cleanup_factory: Callable[[], Awaitable[ChannelCleanupResult]],
    ) -> None:
        try:
            await self._initialize_cleanup_state(channel_label)
        except RuntimeError:
            await self._reply_to_event(
                control_event,
                "A cleanup job is already running. Use status/s, pause/p, resume/r, or stop/x.",
            )
            return

        control_chat_id = int(getattr(control_event, "chat_id", 0))
        reply_to_message_id = getattr(getattr(control_event, "message", None), "id", None)
        command_message_id = getattr(getattr(control_event, "message", None), "id", None)
        self._progress_target_chat_id = target_chat_id

        if command_message_id is not None and target_chat_id is not None:
            self._protect_message(target_chat_id, command_message_id)

        try:
            progress_message = await self._send_message(
                control_chat_id,
                "Starting cleanup...",
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            await self._finalize_cleanup_state()
            self._progress_target_chat_id = None
            raise

        self._progress_message_chat_id = control_chat_id
        self._progress_message_id = getattr(progress_message, "id", None)
        self._last_progress_text = "Starting cleanup..."
        self._last_progress_publish = time.monotonic()

        if (
            target_chat_id is not None
            and self._progress_message_id is not None
            and control_chat_id == target_chat_id
        ):
            self._protect_message(target_chat_id, self._progress_message_id)

        self._cleanup_task = asyncio.create_task(self._run_command_cleanup(cleanup_factory))

    async def _run_command_cleanup(
        self,
        cleanup_factory: Callable[[], Awaitable[ChannelCleanupResult]],
    ) -> ChannelCleanupResult | None:
        result: ChannelCleanupResult | None = None
        error_message: str | None = None
        stopped = False

        try:
            result = await cleanup_factory()
            stopped = self._stop_requested
            return result
        except Exception as error:
            LOGGER.exception("Command cleanup failed: %s", error)
            error_message = str(error)
            return None
        finally:
            await self._publish_completion(result, stopped=stopped, error_message=error_message)
            await self._finalize_cleanup_state()
            self._cleanup_task = None
            self._release_protected_messages(self._progress_target_chat_id)
            self._progress_target_chat_id = None

    async def _run_tracked_cleanup(
        self,
        channel_label: str,
        cleanup_factory: Callable[[], Awaitable[ChannelCleanupResult]],
    ) -> ChannelCleanupResult:
        await self._initialize_cleanup_state(channel_label)
        try:
            return await cleanup_factory()
        finally:
            await self._finalize_cleanup_state()

    async def _initialize_cleanup_state(self, channel_label: str) -> None:
        async with self._state_lock:
            if bool(self.state["running"]):
                raise RuntimeError("A cleanup job is already running.")

            self._stop_requested = False
            self.state["running"] = True
            self.state["paused"] = False
            self.state["current_channel"] = channel_label
            self.state["deleted"] = 0
            self.state["failed"] = 0
            self.state["total"] = 0

    async def _finalize_cleanup_state(self) -> None:
        async with self._state_lock:
            self.state["running"] = False
            self.state["paused"] = False
        self._progress_message_chat_id = None
        self._progress_message_id = None
        self._last_progress_text = ""
        self._last_progress_publish = 0.0

    async def _clean_all_messages(
        self,
        entity: Any,
        channel_label: str,
        *,
        target_chat_id: int | None = None,
    ) -> ChannelCleanupResult:
        protected_ids = self._protected_ids(target_chat_id)
        max_message_id = await self._latest_message_id(entity, channel_label)
        total_messages = await self._count_messages(entity, channel_label)
        if protected_ids:
            total_messages = max(total_messages - len(protected_ids), 0)

        await self._update_state(total=total_messages)

        if total_messages == 0:
            tqdm.write(f"{channel_label} is already empty.")
            await self._maybe_publish_progress(force=True)
            return ChannelCleanupResult(
                channel=channel_label,
                total_messages=0,
                deleted_messages=0,
                failed_messages=0,
            )

        deleted_messages = 0
        failed_messages = 0
        min_message_id = 0

        with tqdm(
            total=total_messages,
            desc=f"Deleting {channel_label}",
            unit="msg",
            dynamic_ncols=True,
        ) as progress_bar:
            while True:
                if self._should_stop():
                    break
                await self._wait_if_paused()
                if self._should_stop():
                    break

                messages = await self._fetch_oldest_batch(
                    entity,
                    channel_label,
                    min_message_id,
                    max_message_id=max_message_id,
                )
                all_message_ids = [
                    message.id for message in messages if getattr(message, "id", None) is not None
                ]
                if not all_message_ids:
                    break

                min_message_id = all_message_ids[-1]
                message_ids = [message_id for message_id in all_message_ids if message_id not in protected_ids]
                if not message_ids:
                    continue

                deleted_in_batch = await self._delete_batch(entity, channel_label, message_ids)
                deleted_messages += deleted_in_batch
                failed_messages += len(message_ids) - deleted_in_batch
                progress_bar.update(len(message_ids))
                progress_bar.set_postfix_str(f"deleted={deleted_messages} failed={failed_messages}")
                await self._update_state(
                    deleted=deleted_messages,
                    failed=failed_messages,
                    total=total_messages,
                )
                await self._maybe_publish_progress()

        await self._maybe_publish_progress(force=True)
        return ChannelCleanupResult(
            channel=channel_label,
            total_messages=total_messages,
            deleted_messages=deleted_messages,
            failed_messages=failed_messages,
        )

    async def _clean_recent_messages(
        self,
        entity: Any,
        channel_label: str,
        limit: int,
        *,
        target_chat_id: int | None = None,
    ) -> ChannelCleanupResult:
        protected_ids = self._protected_ids(target_chat_id)
        message_ids = await self._collect_recent_message_ids(
            entity,
            channel_label,
            limit=limit,
            protected_ids=protected_ids,
        )
        total_messages = len(message_ids)
        await self._update_state(total=total_messages)

        if total_messages == 0:
            tqdm.write(f"No messages available to delete in {channel_label}.")
            await self._maybe_publish_progress(force=True)
            return ChannelCleanupResult(
                channel=channel_label,
                total_messages=0,
                deleted_messages=0,
                failed_messages=0,
            )

        deleted_messages = 0
        failed_messages = 0

        with tqdm(
            total=total_messages,
            desc=f"Deleting recent messages in {channel_label}",
            unit="msg",
            dynamic_ncols=True,
        ) as progress_bar:
            for batch in self._chunked(message_ids, self.batch_size):
                if self._should_stop():
                    break
                await self._wait_if_paused()
                if self._should_stop():
                    break

                deleted_in_batch = await self._delete_batch(entity, channel_label, batch)
                deleted_messages += deleted_in_batch
                failed_messages += len(batch) - deleted_in_batch
                progress_bar.update(len(batch))
                progress_bar.set_postfix_str(f"deleted={deleted_messages} failed={failed_messages}")
                await self._update_state(
                    deleted=deleted_messages,
                    failed=failed_messages,
                    total=total_messages,
                )
                await self._maybe_publish_progress()

        await self._maybe_publish_progress(force=True)
        return ChannelCleanupResult(
            channel=channel_label,
            total_messages=total_messages,
            deleted_messages=deleted_messages,
            failed_messages=failed_messages,
        )

    async def _collect_recent_message_ids(
        self,
        entity: Any,
        channel_label: str,
        *,
        limit: int,
        protected_ids: set[int],
    ) -> list[int]:
        recent_message_ids: list[int] = []
        max_message_id: int | None = None

        while len(recent_message_ids) < limit:
            if self._should_stop():
                break
            await self._wait_if_paused()
            if self._should_stop():
                break

            messages = await self._fetch_recent_batch(entity, channel_label, max_message_id)
            all_message_ids = [
                message.id for message in messages if getattr(message, "id", None) is not None
            ]
            if not all_message_ids:
                break

            max_message_id = min(all_message_ids)
            for message_id in all_message_ids:
                if message_id in protected_ids:
                    continue
                recent_message_ids.append(message_id)
                if len(recent_message_ids) >= limit:
                    break

        return recent_message_ids[:limit]

    async def _fetch_oldest_batch(
        self,
        entity: Any,
        channel_label: str,
        min_message_id: int,
        *,
        max_message_id: int | None,
    ) -> Sequence[Any]:
        return await self._call(
            label=f"fetch_messages:{channel_label}",
            operation=(
                lambda: self._client.get_messages(
                    entity,
                    limit=self.batch_size,
                    min_id=min_message_id,
                    reverse=True,
                )
                if max_message_id is None
                else self._client.get_messages(
                    entity,
                    limit=self.batch_size,
                    min_id=min_message_id,
                    max_id=max_message_id + 1,
                    reverse=True,
                )
            ),
        )

    async def _fetch_recent_batch(
        self,
        entity: Any,
        channel_label: str,
        max_message_id: int | None,
    ) -> Sequence[Any]:
        return await self._call(
            label=f"fetch_recent_messages:{channel_label}",
            operation=(
                lambda: self._client.get_messages(entity, limit=self.batch_size)
                if max_message_id is None
                else self._client.get_messages(
                    entity,
                    limit=self.batch_size,
                    max_id=max_message_id,
                )
            ),
        )

    async def _delete_batch(self, entity: Any, channel_label: str, message_ids: Sequence[int]) -> int:
        if not message_ids:
            return 0

        try:
            await self._call(
                label=f"delete_batch:{channel_label}",
                operation=lambda: self._client.delete_messages(entity, list(message_ids), revoke=True),
            )
            return len(message_ids)
        except Exception as batch_error:
            LOGGER.warning(
                "Batch delete failed for %s (%s). Falling back to per-message deletion.",
                channel_label,
                batch_error,
            )

        deleted_messages = 0
        for message_id in message_ids:
            if self._should_stop():
                break
            await self._wait_if_paused()
            if self._should_stop():
                break

            try:
                await self._call(
                    label=f"delete_message:{channel_label}:{message_id}",
                    operation=lambda current_id=message_id: self._client.delete_messages(
                        entity,
                        [current_id],
                        revoke=True,
                    ),
                )
                deleted_messages += 1
            except Exception as message_error:
                LOGGER.error(
                    "Unable to delete message %s in %s: %s",
                    message_id,
                    channel_label,
                    message_error,
                )
        return deleted_messages

    async def _count_messages(self, entity: Any, channel_label: str) -> int:
        messages = await self._call(
            label=f"count_messages:{channel_label}",
            operation=lambda: self._client.get_messages(entity, limit=1),
        )
        total = getattr(messages, "total", None)
        if total is None:
            return len(messages)
        return int(total)

    async def _latest_message_id(self, entity: Any, channel_label: str) -> int | None:
        messages = await self._call(
            label=f"latest_message:{channel_label}",
            operation=lambda: self._client.get_messages(entity, limit=1),
        )
        if not messages:
            return None

        latest = messages[0]
        message_id = getattr(latest, "id", None)
        if isinstance(message_id, int) and message_id > 0:
            return message_id
        return None

    async def _resolve_event_target(self, event: Any) -> tuple[Any, str, int]:
        entity = await event.get_input_chat()
        chat = await event.get_chat()
        chat_id = int(getattr(event, "chat_id", 0))
        channel_label = self._format_channel_label(chat, str(chat_id))
        return entity, channel_label, chat_id

    async def _resolve_chat_target(self, chat: Any) -> tuple[Any, str, int | None]:
        if hasattr(chat, "get_input_chat"):
            entity = await chat.get_input_chat()
            resolved_chat = await chat.get_chat() if hasattr(chat, "get_chat") else None
            chat_id = getattr(chat, "chat_id", None)
            channel_label = self._format_channel_label(resolved_chat, str(chat_id or "current-chat"))
            return entity, channel_label, chat_id

        entity, channel_label, chat_id = await self._resolve_entity_target(chat)
        return entity, channel_label, chat_id

    async def _resolve_entity_target(self, channel_reference: Any) -> tuple[Any, str, int | None]:
        entity = await self._call(
            label=f"resolve_channel:{channel_reference}",
            operation=lambda: self._client.get_entity(channel_reference),
        )
        channel_label = self._format_channel_label(entity, str(channel_reference))
        return entity, channel_label, getattr(entity, "id", None)

    async def _wait_if_paused(self) -> None:
        while bool(self.state["paused"]):
            if self._should_stop():
                return
            await asyncio.sleep(1)

    def _should_stop(self) -> bool:
        return self._stop_requested or not bool(self.state["running"])

    async def _update_state(
        self,
        *,
        deleted: int | None = None,
        failed: int | None = None,
        total: int | None = None,
    ) -> None:
        async with self._state_lock:
            if deleted is not None:
                self.state["deleted"] = deleted
            if failed is not None:
                self.state["failed"] = failed
            if total is not None:
                self.state["total"] = total

    async def _state_snapshot(self) -> dict[str, bool | str | int]:
        async with self._state_lock:
            return dict(self.state)

    async def _maybe_publish_progress(self, *, force: bool = False) -> None:
        snapshot = await self._state_snapshot()
        text = self._render_status(snapshot, header="Cleanup in progress")
        now = time.monotonic()

        if not force and now - self._last_progress_publish < PROGRESS_UPDATE_INTERVAL:
            return
        if not force and text == self._last_progress_text:
            return

        tqdm.write(
            f"[progress] {snapshot['current_channel']} deleted={snapshot['deleted']} "
            f"failed={snapshot['failed']} total={snapshot['total']}"
        )

        if self._progress_message_chat_id is None or self._progress_message_id is None:
            self._last_progress_text = text
            self._last_progress_publish = now
            return

        try:
            await self._call(
                label="edit_progress_message",
                operation=lambda: self._client.edit_message(
                    self._progress_message_chat_id,
                    self._progress_message_id,
                    text,
                ),
            )
            self._last_progress_text = text
            self._last_progress_publish = now
        except Exception as error:
            LOGGER.warning("Unable to update progress message: %s", error)

    async def _publish_completion(
        self,
        result: ChannelCleanupResult | None,
        *,
        stopped: bool,
        error_message: str | None,
    ) -> None:
        snapshot = await self._state_snapshot()

        if error_message:
            text = self._render_status(snapshot, header=f"Cleanup failed: {error_message}")
        elif stopped:
            text = self._render_status(snapshot, header="Cleanup stopped")
        else:
            text = self._render_status(snapshot, header="Cleanup finished")

        if self._progress_message_chat_id is not None and self._progress_message_id is not None:
            try:
                await self._call(
                    label="finalize_progress_message",
                    operation=lambda: self._client.edit_message(
                        self._progress_message_chat_id,
                        self._progress_message_id,
                        text,
                    ),
                )
                return
            except Exception as error:
                LOGGER.warning("Unable to finalize progress message: %s", error)

        if self._progress_message_chat_id is not None:
            await self._send_message(self._progress_message_chat_id, text)

    async def _format_status_text(self, *, header: str) -> str:
        snapshot = await self._state_snapshot()
        return self._render_status(snapshot, header=header)

    def _render_status(self, snapshot: dict[str, bool | str | int], *, header: str) -> str:
        total = int(snapshot.get("total", 0))
        deleted = int(snapshot.get("deleted", 0))
        failed = int(snapshot.get("failed", 0))
        processed = deleted + failed
        percent = (processed / total * 100.0) if total > 0 else 0.0

        if bool(snapshot.get("running")):
            lifecycle = "paused" if bool(snapshot.get("paused")) else "running"
        else:
            lifecycle = "idle"

        total_label = str(total) if total > 0 else "unknown"
        channel_label = str(snapshot.get("current_channel") or "n/a")

        return (
            f"{header}\n"
            f"State: {lifecycle}\n"
            f"Channel: {channel_label}\n"
            f"Deleted: {deleted}\n"
            f"Failed: {failed}\n"
            f"Total: {total_label}\n"
            f"Progress: {percent:.2f}%"
        )

    async def _reply_to_event(self, event: Any, text: str) -> Any:
        chat_id = int(getattr(event, "chat_id", 0))
        reply_to_message_id = getattr(getattr(event, "message", None), "id", None)
        return await self._send_message(chat_id, text, reply_to_message_id=reply_to_message_id)

    async def _send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> Any:
        return await self._call(
            label=f"send_message:{chat_id}",
            operation=lambda: self._client.send_message(chat_id, text, reply_to=reply_to_message_id),
        )

    def _parse_command(self, raw_text: str) -> tuple[str, str | None] | None:
        parts = raw_text.strip().split(maxsplit=1)
        if not parts or not parts[0]:
            return None

        raw_command = parts[0].lower()
        command = self.COMMAND_ALIASES.get(raw_command, raw_command)
        if command not in {"deleteall", "delete", "clean", "status", "help", "stop", "pause", "resume"}:
            return None

        argument = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        return command, argument

    def _help_text(self) -> str:
        return (
            "Commands:\n"
            "da -> delete all\n"
            "d <n> -> delete last n\n"
            "c <channel> -> clean channel\n"
            "s -> status\n"
            "h -> help\n"
            "p -> pause\n"
            "r -> resume\n"
            "x -> stop"
        )

    def _protected_ids(self, chat_id: int | None) -> set[int]:
        if chat_id is None:
            return set()
        return set(self._protected_message_ids.get(chat_id, set()))

    def _protect_message(self, chat_id: int, message_id: int) -> None:
        self._protected_message_ids.setdefault(chat_id, set()).add(message_id)

    def _release_protected_messages(self, chat_id: int | None) -> None:
        if chat_id is None:
            return
        self._protected_message_ids.pop(chat_id, None)

    def _format_channel_label(self, entity: Any, fallback: str) -> str:
        username = getattr(entity, "username", None)
        if username:
            return f"@{username}"

        title = getattr(entity, "title", None)
        if title:
            return str(title)

        first_name = getattr(entity, "first_name", None)
        last_name = getattr(entity, "last_name", None)
        full_name = " ".join(part for part in (first_name, last_name) if part)
        if full_name:
            return full_name

        return fallback

    def _chunked(self, values: Sequence[int], chunk_size: int) -> list[list[int]]:
        return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]

    async def _call(self, *, label: str, operation: Callable[[], Awaitable[Any]]) -> Any:
        while True:
            try:
                return await self.app.client.safe_request(
                    operation,
                    label=label,
                    backend="userbot",
                )
            except Exception as error:
                wait_seconds = _extract_floodwait_seconds(error)
                if wait_seconds is None:
                    raise

                LOGGER.warning("FloodWait while processing %s. Sleeping for %s seconds.", label, wait_seconds)
                await asyncio.sleep(wait_seconds + 1)

    @property
    def _client(self) -> Any:
        if self._user_client is None:
            raise RuntimeError("Telegram userbot client is not connected.")
        return self._user_client


TeleBridgeChannelCleaner = TelegramCleaner


def configure_logging(log_level: str = "WARNING") -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.WARNING),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def run_cleaner(
    config: CleanerConfig,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    log_level: str = "WARNING",
) -> int:
    if not config.channels:
        raise ValueError("No channels configured for cleanup.")

    cleaner = TelegramCleaner(config, batch_size=batch_size, log_level=log_level)

    try:
        results = await cleaner.run()
    finally:
        await cleaner.stop()

    total_deleted = sum(result.deleted_messages for result in results)
    total_failed = sum(result.failed_messages for result in results)
    channel_errors = sum(1 for result in results if result.error)

    if channel_errors or total_failed:
        print(
            f"\nFinished cleanup with issues. Deleted={total_deleted}, "
            f"failed={total_failed}, channel_errors={channel_errors}"
        )
        return 1

    print("\nFinished cleanup.")
    return 0


async def run_command_mode(
    config: CleanerConfig,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    log_level: str = "WARNING",
) -> int:
    cleaner = TelegramCleaner(config, batch_size=batch_size, log_level=log_level)

    try:
        await cleaner.run_command_mode()
    finally:
        await cleaner.stop()

    return 0


def _extract_floodwait_seconds(error: Exception) -> int | None:
    name = type(error).__name__.lower()
    if "floodwait" not in name:
        return None

    for attribute in ("seconds", "value", "retry_after"):
        raw_value = getattr(error, attribute, None)
        if isinstance(raw_value, int) and raw_value > 0:
            return raw_value

    return 1


async def list_channels(cleaner: TelegramCleaner) -> list[AvailableChannel]:
    dialogs = await cleaner._call(
        label="list_dialogs",
        operation=lambda: cleaner._client.get_dialogs(),
    )

    channels: list[AvailableChannel] = []
    seen_labels: set[str] = set()

    for dialog in dialogs:
        entity = getattr(dialog, "entity", None)
        if entity is None or not _is_admin_channel(entity):
            continue

        label = cleaner._format_channel_label(entity, str(getattr(entity, "id", "unknown")))
        unique_key = f"{getattr(entity, 'id', 'unknown')}:{label}"
        if unique_key in seen_labels:
            continue

        channels.append(AvailableChannel(reference=entity, label=label))
        seen_labels.add(unique_key)

    channels.sort(key=lambda item: item.label.casefold())
    return channels


def _is_admin_channel(entity: Any) -> bool:
    has_channel_shape = any(
        bool(getattr(entity, attribute, False))
        for attribute in ("broadcast", "megagroup", "gigagroup")
    )
    if not has_channel_shape and not hasattr(entity, "admin_rights") and not hasattr(entity, "creator"):
        return False

    return bool(getattr(entity, "creator", False) or getattr(entity, "admin_rights", None))
