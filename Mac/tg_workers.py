import asyncio
import difflib
import io
import json
import os
import re
import time

from tg_shared import (
    PREVIEW_IMAGE_LIMIT,
    PREVIEW_IMAGE_WINDOW_SECONDS,
    download_media_size as _download_media_size,
    dest_index_has as _dest_index_has,
    dest_index_import_sent_files as _dest_index_import_sent_files,
    dest_index_replace_topic as _dest_index_replace_topic,
    dest_index_track as _dest_index_track,
    fetch_all_topics as _fetch_all_topics,
    fmt_bytes as _fmt_bytes,
    get_fn as _get_fn,
    get_src_thread as _get_src_thread,
    get_sz as _get_sz,
    is_download_file as _is_download_file,
    is_dup as _is_dup,
    is_gif_media as _is_gif_media,
    is_image_document as _is_image_document,
    is_no_retry_send_error as _is_no_retry_send_error,
    is_standalone_decoration_media as _is_standalone_decoration_media,
    is_video_media as _is_video_media,
    media_kind as _media_kind,
    message_needs_link_repair as _message_needs_link_repair,
    message_text_with_links as _message_text_with_links,
    new_loop as _new_loop,
    photo_size as _photo_size,
    load_dest_index as _load_dest_index,
    safe_name as _safe_name,
    safe_req as _safe_req,
    save_json_file as _save_json_file,
    save_dest_index as _save_dest_index,
    send_error_label as _send_error_label,
    track as _track,
)


def _photo_upload_too_large_error(err):
    text = str(err).lower()
    return "photo you tried to send cannot be saved" in text or "exceeds 10mb" in text


def _image_processing_error(err):
    text = str(err).lower()
    return "failure while processing image" in text or "image_process_failed" in text


# -- Mode A: Auto backup --------------------------------------------------------
def run_backup_auto(api_id, api_hash, source_id, dest_id, state_file, log_q, stop_event, filters):
    # filters = {"messages":bool, "images":bool, "files":bool, "skip_duplicates":bool}
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import CreateForumTopicRequest
            from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); log_q.put("__DONE__"); return
        thread_map = {}; last_msg_id = [0]; topic_last_msg_id = {}; sent_files = {}
        def load_state():
            if os.path.exists(state_file):
                with open(state_file, encoding="utf-8") as f:
                    state = json.load(f)
                thread_map.update({int(k): v for k,v in state.get("thread_map",{}).items()})
                last_msg_id[0] = state.get("last_msg_id", 0)
                topic_last_msg_id.update({int(k): v for k,v in state.get("topic_last_msg_id",{}).items()})
                for k,v in state.get("sent_files",{}).items():
                    sent_files[str(k)] = v if isinstance(v,dict) else {}
                log_q.put(f"Resuming from message {last_msg_id[0]}, {len(thread_map)} topics in map")
            else:
                log_q.put("No saved state — starting fresh")
        def save_state():
            _save_json_file(
                state_file,
                {"thread_map":thread_map,"last_msg_id":last_msg_id[0],"topic_last_msg_id":topic_last_msg_id,"sent_files":sent_files},
            )
        async def safe(fn): return await _safe_req(fn, stop_event, log_q)
        async def count_pending_messages(src_thread, topic_last, has_topics):
            count = 0
            if has_topics:
                msg_iter = client.iter_messages(source_id, reverse=True, min_id=topic_last, reply_to=src_thread)
            else:
                msg_iter = client.iter_messages(source_id, reverse=True, min_id=topic_last)
            async for _ in msg_iter:
                if stop_event.is_set():
                    break
                count += 1
            return count
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); log_q.put("__DONE__"); return
            load_state()
            src = await client.get_entity(source_id)
            dst = await client.get_entity(dest_id)
            dst_input = await client.get_input_entity(dest_id)
            is_forum = True
            src_topics = {}
            # Check if source has topics
            try:
                from telethon.tl.functions.messages import GetForumTopicsRequest
                await client(GetForumTopicsRequest(peer=src, offset_date=None, offset_id=0, offset_topic=0, limit=1))
            except Exception:
                is_forum = False
                log_q.put("Source has no Topics — all messages go to General.")
                thread_map[1] = 1
            if is_forum:
                log_q.put("Syncing topic map...")
                # Fetch ALL source topics
                src_topics = await _fetch_all_topics(client, src, log_q)
                known_topic_ids = set(src_topics.keys())
                log_q.put(f"Source topics found: {len(src_topics)}")
                # Create any missing ones in destination
                for tid, title in src_topics.items():
                    if tid == 1: thread_map[1] = 1; continue
                    if tid in thread_map: continue
                    log_q.put(f"Creating topic: '{title}'")
                    new = await safe(lambda t=title: client(CreateForumTopicRequest(peer=dst_input, title=t)))
                    nid = new.updates[0].id
                    thread_map[tid] = nid
                    log_q.put(f"  -> dest id {nid}")
                    save_state(); await asyncio.sleep(0.5)
                log_q.put(f"Topics ready: {len(thread_map)} mapped")
            else:
                known_topic_ids = {1}
            log_q.put("Counting remaining messages...")
            remaining = 0
            topic_pairs = []
            if is_forum:
                for src_thread in sorted(src_topics.keys()):
                    dest_thread = thread_map.get(src_thread)
                    if dest_thread is None or (src_thread != 1 and dest_thread == 1):
                        raise RuntimeError(f"Topic {src_thread} exists in source but is not mapped; stopping to avoid General")
                    topic_last = topic_last_msg_id.get(src_thread, last_msg_id[0])
                    topic_total = await count_pending_messages(src_thread, topic_last, True)
                    remaining += topic_total
                    topic_pairs.append((src_thread, dest_thread, topic_last, topic_total, src_topics.get(src_thread, "General")))
            else:
                remaining = await count_pending_messages(1, last_msg_id[0], False)
                topic_pairs.append((1, 1, last_msg_id[0], remaining, "General"))
            log_q.put(f"Messages to copy: {remaining}\n")
            async def iter_topic_messages():
                for src_thread, dest_thread, topic_last, topic_total, topic_title in topic_pairs:
                    if stop_event.is_set(): return
                    if is_forum:
                        if topic_total:
                            log_q.put(f"Copying topic: {topic_title} — {topic_total} messages pending this run")
                        else:
                            log_q.put(f"Topic: {topic_title} — no messages pending this run")
                        msg_iter = client.iter_messages(source_id, reverse=True, min_id=topic_last, reply_to=src_thread)
                    else:
                        log_q.put(f"Copying messages: {topic_total} messages pending this run")
                        msg_iter = client.iter_messages(source_id, reverse=True, min_id=topic_last)
                    async for msg in msg_iter:
                        if stop_event.is_set(): return
                        yield src_thread, dest_thread, topic_total, topic_title, msg
            copied = skipped = errors = 0; processed = 0
            topic_progress = {}
            topic_skipped = {}
            topic_errors = {}
            current_src_thread = None
            before_file_images = []
            after_file_image_slots = 0
            after_file_anchor_date = None
            def reply_to_msg_id(message):
                reply = getattr(message, "reply_to", None)
                return getattr(reply, "reply_to_msg_id", None) if reply else None
            def close_to_file(image_record, file_msg):
                image_date = image_record.get("date")
                file_date = getattr(file_msg, "date", None)
                if not image_date or not file_date:
                    return False
                return abs((file_date - image_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
            def close_to_last_file(message):
                if not after_file_anchor_date or not getattr(message, "date", None):
                    return False
                return abs((message.date - after_file_anchor_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
            async def send_preview_image(record, dest_thread):
                nonlocal copied, skipped, errors
                try:
                    if filters.get("convert_image_files", False) and _is_image_document(record["media"]) and record.get("message"):
                        data = await client.download_media(record["message"], file=bytes)
                        name = _get_fn(record["media"]) or "image.jpg"
                        async def send_as_photo():
                            bio = io.BytesIO(data)
                            bio.name = name
                            return await client.send_file(
                                dest_id, bio, caption=record["text"], reply_to=dest_thread if dest_thread else 1,
                                force_document=False)
                        try:
                            await send_as_photo()
                        except Exception as photo_err:
                            if not _photo_upload_too_large_error(photo_err):
                                raise
                            log_q.put(f"WARN [{record.get('topic_title','preview')}] image too large as photo; sending as file instead")
                            await client.send_file(
                                dest_id, record["media"], caption=record["text"], reply_to=dest_thread if dest_thread else 1,
                                force_document=True)
                    else:
                        await client.send_file(
                            dest_id, record["media"], caption=record["text"], reply_to=dest_thread if dest_thread else 1,
                            force_document=False)
                    copied += 1
                    return True
                except InterruptedError:
                    raise
                except Exception as img_err:
                    skipped += 1
                    log_q.put(f"WARN [{record.get('topic_title','preview')}] skipped preview msg {record['id']}: {img_err}")
                    return False
            def skip_buffered_previews(src_thread):
                nonlocal skipped, before_file_images
                if before_file_images:
                    skipped += len(before_file_images)
                    topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + len(before_file_images)
                    before_file_images = []
            def progress_line(topic_title, topic_done, topic_total, t_skipped, t_errors):
                topic_pct = min(100.0, round((topic_done / topic_total) * 100, 1)) if topic_total else 100.0
                run_pct = min(100.0, round((processed / remaining) * 100, 1)) if remaining else 100.0
                return (f"Progress: [{topic_title}] {topic_done}/{topic_total} this topic ({topic_pct}%) | "
                        f"{t_skipped} skipped, {t_errors} errors | "
                        f"{processed}/{remaining} this run ({run_pct}%)")
            async for src_thread, dest_thread, topic_total, topic_title, msg in iter_topic_messages():
                if stop_event.is_set():
                    log_q.put(f"Stopped at message {last_msg_id[0]}."); break
                try:
                    if current_src_thread != src_thread:
                        if current_src_thread is not None:
                            skip_buffered_previews(current_src_thread)
                        current_src_thread = src_thread
                        before_file_images = []
                        after_file_image_slots = 0
                        after_file_anchor_date = None
                    from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                    media = msg.media
                    text = _message_text_with_links(msg)
                    if isinstance(media, MessageMediaWebPage): media = None
                    # Apply filters
                    noise_media = media and _is_standalone_decoration_media(media)
                    if noise_media and text:
                        media = None
                        noise_media = False
                    is_photo = isinstance(media, MessageMediaPhoto) or _is_image_document(media)
                    is_file = isinstance(media, MessageMediaDocument) and not _is_image_document(media)
                    is_text = not media and bool(text)
                    skip_this = False
                    if noise_media: skip_this = True
                    if is_text and not filters.get("messages", True): skip_this = True
                    if is_photo and not filters.get("images", True): skip_this = True
                    if is_file and not filters.get("files", True): skip_this = True
                    if skip_this:
                        topic_last_msg_id[src_thread] = msg.id
                        if not is_forum:
                            last_msg_id[0] = max(last_msg_id[0], msg.id)
                        save_state(); processed += 1; skipped += 1
                        topic_progress[src_thread] = topic_progress.get(src_thread, 0) + 1
                        topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                        if processed % 10 == 0 or topic_progress[src_thread] == topic_total or processed == remaining:
                            log_q.put(progress_line(topic_title, topic_progress[src_thread], topic_total,
                                                    topic_skipped.get(src_thread, 0), topic_errors.get(src_thread, 0)))
                        await asyncio.sleep(0.1); continue
                    if dest_thread == 1 and src_thread != 1:
                        log_q.put(f"  WARNING: topic {src_thread} not mapped — General (msg {msg.id})")
                    kwargs = {"reply_to": dest_thread if dest_thread else 1}
                    if is_photo:
                        record = {"id": msg.id, "media": media, "text": text, "date": msg.date, "topic_title": topic_title, "message": msg}
                        if after_file_image_slots > 0 and close_to_last_file(msg):
                            await send_preview_image(record, dest_thread)
                            after_file_image_slots -= 1
                        else:
                            if after_file_image_slots > 0:
                                after_file_image_slots = 0
                            before_file_images.append(record)
                            if len(before_file_images) > PREVIEW_IMAGE_LIMIT:
                                before_file_images.pop(0)
                                skipped += 1
                                topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                    elif media:
                        fn = _get_fn(media); sz = _get_sz(media)
                        if filters.get("skip_duplicates", True) and _is_dup(sent_files, dest_thread, fn, sz):
                            log_q.put(f"  Skipping duplicate: {fn}")
                            skipped += 1
                            topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                            skip_buffered_previews(src_thread)
                            after_file_image_slots = 0
                        else:
                            after_file_image_slots = 0
                            await safe(lambda: client.send_file(dest_id, media, caption=text, **kwargs))
                            _track(sent_files, dest_thread, fn, sz)
                            copied += 1
                            file_reply_id = reply_to_msg_id(msg)
                            selected_previews = [
                                preview for preview in before_file_images
                                if preview["id"] == file_reply_id or close_to_file(preview, msg)
                            ][-PREVIEW_IMAGE_LIMIT:]
                            skipped_previews = len(before_file_images) - len(selected_previews)
                            if skipped_previews > 0:
                                skipped += skipped_previews
                                topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + skipped_previews
                            for preview in selected_previews:
                                await send_preview_image(preview, dest_thread)
                            before_file_images = []
                            after_file_anchor_date = msg.date
                            after_file_image_slots = max(0, PREVIEW_IMAGE_LIMIT - len(selected_previews)) if filters.get("images", True) else 0
                    else:
                        if text:
                            await safe(lambda: client.send_message(dest_id, text, **kwargs))
                            copied += 1
                    topic_last_msg_id[src_thread] = msg.id
                    if not is_forum:
                        last_msg_id[0] = max(last_msg_id[0], msg.id)
                    save_state()
                    processed += 1
                    topic_progress[src_thread] = topic_progress.get(src_thread, 0) + 1
                    if processed % 10 == 0 or topic_progress[src_thread] == topic_total or processed == remaining:
                        log_q.put(progress_line(topic_title, topic_progress[src_thread], topic_total,
                                                topic_skipped.get(src_thread, 0), topic_errors.get(src_thread, 0)))
                    await asyncio.sleep(1)
                except InterruptedError: break
                except Exception as e:
                    skip_buffered_previews(src_thread)
                    after_file_image_slots = 0
                    err_str = str(e)
                    if "premium account is required" in err_str.lower():
                        log_q.put(f"  Skipping msg {msg.id}: requires Telegram Premium (large file or exclusive content)")
                        skipped += 1
                        topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                    elif _is_no_retry_send_error(e):
                        log_q.put(f"ERROR on msg {msg.id}: {_send_error_label(e)}")
                        errors += 1
                        topic_errors[src_thread] = topic_errors.get(src_thread, 0) + 1
                        topic_last_msg_id[src_thread] = msg.id
                        if not is_forum:
                            last_msg_id[0] = max(last_msg_id[0], msg.id)
                        save_state()
                        processed += 1
                        topic_progress[src_thread] = topic_progress.get(src_thread, 0) + 1
                        if processed % 10 == 0 or topic_progress[src_thread] == topic_total or processed == remaining:
                            log_q.put(progress_line(topic_title, topic_progress[src_thread], topic_total,
                                                    topic_skipped.get(src_thread, 0), topic_errors.get(src_thread, 0)))
                    else:
                        errors += 1
                        topic_errors[src_thread] = topic_errors.get(src_thread, 0) + 1
                        log_q.put(f"ERROR on msg {msg.id}: {e}")
            if current_src_thread is not None:
                skip_buffered_previews(current_src_thread)
            save_state()
            log_q.put(f"\nDone. {copied} copied, {skipped} skipped, {errors} errors across {len(topic_pairs)} route(s).")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")
    _new_loop(_r())
# -- Mode B: Manual mapping -----------------------------------------------------
def run_backup_manual(api_id, api_hash, mappings, state_file, log_q, stop_event, filters):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetForumTopicsRequest
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); log_q.put("__DONE__"); return
        state = {}
        if os.path.exists(state_file):
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            log_q.put(f"State loaded for {len(state)} mappings")
        else:
            log_q.put("No saved state — starting fresh")
        def save_state():
            _save_json_file(state_file, state)
        async def safe(fn): return await _safe_req(fn, stop_event, log_q)
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); log_q.put("__DONE__"); return
            for m in mappings:
                if stop_event.is_set(): break
                key = f"{m['src_group_id']}_{m['src_topic_id']}_{m['dest_group_id']}_{m['dest_topic_id']}"
                ms = state.setdefault(key, {"last_msg_id":0,"sent_files":{}})
                last_id = ms["last_msg_id"]
                sent_files = ms["sent_files"]
                src_name = m.get('src_group_name','Unknown')
                src_t    = m.get('src_topic_title','General')
                dst_t    = m.get('dest_topic_title','Unknown')
                log_q.put(f"\n-- {src_name} / {src_t} -> {dst_t} --")
                log_q.put(f"   Resuming from message {last_id}")
                src_topic_id  = m['src_topic_id']
                dest_topic_id = m['dest_topic_id']
                dest_group_id = m['dest_group_id']
                source_has_topics = False
                try:
                    src_entity = await client.get_entity(m['src_group_id'])
                    await client(GetForumTopicsRequest(peer=src_entity, offset_date=None, offset_id=0, offset_topic=0, limit=1))
                    source_has_topics = True
                except Exception:
                    source_has_topics = False
                # If the source is a forum, even topic id 1 means the General topic, not the whole group.
                if source_has_topics:
                    msg_iter = client.iter_messages(m['src_group_id'], reverse=True, min_id=last_id, reply_to=src_topic_id)
                else:
                    msg_iter = client.iter_messages(m['src_group_id'], reverse=True, min_id=last_id)
                copied = skipped = errors = 0; count = 0
                before_file_images = []
                after_file_image_slots = 0
                after_file_anchor_date = None
                def reply_to_msg_id(message):
                    reply = getattr(message, "reply_to", None)
                    return getattr(reply, "reply_to_msg_id", None) if reply else None
                def close_to_file(image_record, file_msg):
                    image_date = image_record.get("date")
                    file_date = getattr(file_msg, "date", None)
                    if not image_date or not file_date:
                        return False
                    return abs((file_date - image_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
                def close_to_last_file(message):
                    if not after_file_anchor_date or not getattr(message, "date", None):
                        return False
                    return abs((message.date - after_file_anchor_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
                async def send_preview_image(record):
                    nonlocal copied, skipped, errors
                    try:
                        if filters.get("convert_image_files", False) and _is_image_document(record["media"]) and record.get("message"):
                            data = await client.download_media(record["message"], file=bytes)
                            name = _get_fn(record["media"]) or "image.jpg"
                            async def send_as_photo():
                                bio = io.BytesIO(data)
                                bio.name = name
                                return await client.send_file(
                                    dest_group_id, bio, caption=record["text"], reply_to=dest_topic_id,
                                    force_document=False)
                            try:
                                await send_as_photo()
                            except Exception as photo_err:
                                if not _photo_upload_too_large_error(photo_err):
                                    raise
                                log_q.put("   WARN image too large as photo; sending as file instead")
                                await client.send_file(
                                    dest_group_id, record["media"], caption=record["text"], reply_to=dest_topic_id,
                                    force_document=True)
                        else:
                            await client.send_file(
                                dest_group_id, record["media"], caption=record["text"], reply_to=dest_topic_id,
                                force_document=False)
                        copied += 1
                        return True
                    except InterruptedError:
                        raise
                    except Exception as img_err:
                        skipped += 1
                        log_q.put(f"   WARN skipped preview msg {record['id']}: {img_err}")
                        return False
                def skip_buffered_previews():
                    nonlocal skipped, before_file_images
                    if before_file_images:
                        skipped += len(before_file_images)
                        before_file_images = []
                async for msg in msg_iter:
                    if stop_event.is_set(): log_q.put("Stopped."); break
                    try:
                        from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                        media = msg.media; text = _message_text_with_links(msg)
                        if isinstance(media, MessageMediaWebPage): media = None
                        noise_media = media and _is_standalone_decoration_media(media)
                        if noise_media and text:
                            media = None
                            noise_media = False
                        is_photo = isinstance(media, MessageMediaPhoto) or _is_image_document(media)
                        is_file  = isinstance(media, MessageMediaDocument) and not _is_image_document(media)
                        is_text  = not media and bool(text)
                        skip_this = False
                        if noise_media: skip_this = True
                        if is_text  and not filters.get("messages", True): skip_this = True
                        if is_photo and not filters.get("images",   True): skip_this = True
                        if is_file  and not filters.get("files",    True): skip_this = True
                        if skip_this:
                            ms["last_msg_id"] = msg.id; save_state(); skipped += 1
                            await asyncio.sleep(0.1); continue
                        kwargs = {"reply_to": dest_topic_id}
                        if is_photo:
                            record = {"id": msg.id, "media": media, "text": text, "date": msg.date, "message": msg}
                            if after_file_image_slots > 0 and close_to_last_file(msg):
                                await send_preview_image(record)
                                after_file_image_slots -= 1
                            else:
                                if after_file_image_slots > 0:
                                    after_file_image_slots = 0
                                before_file_images.append(record)
                                if len(before_file_images) > PREVIEW_IMAGE_LIMIT:
                                    before_file_images.pop(0)
                                    skipped += 1
                        elif media:
                            fn = _get_fn(media); sz = _get_sz(media)
                            if filters.get("skip_duplicates",True) and _is_dup(sent_files, dest_topic_id, fn, sz):
                                log_q.put(f"  Skipping duplicate: {fn}"); skipped += 1
                                skip_buffered_previews()
                                after_file_image_slots = 0
                            else:
                                after_file_image_slots = 0
                                await safe(lambda: client.send_file(dest_group_id, media, caption=text, **kwargs))
                                _track(sent_files, dest_topic_id, fn, sz); copied += 1
                                file_reply_id = reply_to_msg_id(msg)
                                selected_previews = [
                                    preview for preview in before_file_images
                                    if preview["id"] == file_reply_id or close_to_file(preview, msg)
                                ][-PREVIEW_IMAGE_LIMIT:]
                                skipped_previews = len(before_file_images) - len(selected_previews)
                                if skipped_previews > 0:
                                    skipped += skipped_previews
                                for preview in selected_previews:
                                    await send_preview_image(preview)
                                before_file_images = []
                                after_file_anchor_date = msg.date
                                after_file_image_slots = max(0, PREVIEW_IMAGE_LIMIT - len(selected_previews)) if filters.get("images", True) else 0
                        else:
                            if text:
                                await safe(lambda: client.send_message(dest_group_id, text, **kwargs))
                                copied += 1
                        ms["last_msg_id"] = msg.id; ms["sent_files"] = sent_files; save_state()
                        count += 1
                        if count % 10 == 0:
                            log_q.put(f"   Progress: {copied} copied, {skipped} skipped, {errors} errors")
                        await asyncio.sleep(1)
                    except InterruptedError: break
                    except Exception as e:
                        skip_buffered_previews()
                        after_file_image_slots = 0
                        if _is_no_retry_send_error(e):
                            errors += 1
                            ms["last_msg_id"] = msg.id; ms["sent_files"] = sent_files; save_state()
                            count += 1
                            log_q.put(f"   ERROR msg {msg.id}: {_send_error_label(e)}")
                        else:
                            errors += 1; log_q.put(f"   ERROR msg {msg.id}: {e}")
                skip_buffered_previews()
                log_q.put(f"   Done: {copied} copied, {skipped} skipped, {errors} errors")
            save_state()
            log_q.put("\nAll mappings complete.")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")
    _new_loop(_r())
# -- GUI colours ----------------------------------------------------------------
def run_backup_routes(api_id, api_hash, routes, state_file, log_q, stop_event, filters, repair_links=False, repair_missing_files=False):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import CreateForumTopicRequest, GetForumTopicsRequest
            from telethon.tl.types import MessageMediaWebPage
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); log_q.put("__DONE__"); return
        state = {}
        if os.path.exists(state_file):
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            log_q.put(f"State loaded for {len([k for k in state if not k.startswith('__')])} route(s)")
        else:
            log_q.put("No saved state - starting fresh")
        created_topics = state.setdefault("__created_topics", {})
        dest_files = state.setdefault("__dest_files", {})
        dest_scan = state.setdefault("__dest_scan", {})
        dest_index_cache = {}
        def save_state():
            _save_json_file(state_file, state)
        def dest_index_for(group_id):
            group_key = str(group_id)
            if group_key not in dest_index_cache:
                index = _load_dest_index(group_id)
                legacy = dest_files.get(group_key, {})
                if _dest_index_import_sent_files(index, legacy):
                    _save_dest_index(index)
                dest_index_cache[group_key] = index
            return dest_index_cache[group_key]
        def save_dest_index_for(group_id):
            index = dest_index_cache.get(str(group_id))
            if index:
                _save_dest_index(index)
        async def safe(fn): return await _safe_req(fn, stop_event, log_q)
        source_forum_cache = {}
        async def source_has_topics(client, group_id):
            if group_id in source_forum_cache:
                return source_forum_cache[group_id]
            try:
                entity = await client.get_entity(group_id)
                if not getattr(entity, "forum", False):
                    source_forum_cache[group_id] = False
                    return False
                await client(GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=1))
                source_forum_cache[group_id] = True
                return True
            except Exception:
                source_forum_cache[group_id] = False
                return False
        topic_name_cache = {}
        group_name_cache = {}
        async def group_name(client, group_id):
            if group_id in group_name_cache:
                return group_name_cache[group_id]
            try:
                entity = await client.get_entity(group_id)
                name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(group_id)
            except Exception:
                name = str(group_id)
            group_name_cache[group_id] = name
            return name
        async def topic_names(client, group_id):
            if group_id in topic_name_cache:
                return topic_name_cache[group_id]
            try:
                entity = await client.get_entity(group_id)
                if not getattr(entity, "forum", False):
                    topic_name_cache[group_id] = {1: "General"}
                    return topic_name_cache[group_id]
                topics = await _fetch_all_topics(client, entity, log_q)
                topic_name_cache[group_id] = {int(k): v for k, v in topics.items()}
                topic_name_cache[group_id].setdefault(1, "General")
                return topic_name_cache[group_id]
            except Exception:
                topic_name_cache[group_id] = {1: "General"}
                return topic_name_cache[group_id]
        async def verify_topic_id(client, group_id, topic_id, expected_title=""):
            if int(topic_id) == 1:
                return True
            try:
                msg = await client.get_messages(group_id, ids=int(topic_id))
            except Exception:
                return False
            if not msg:
                return False
            action = getattr(msg, "action", None)
            real_title = getattr(action, "title", None)
            if real_title:
                names = topic_name_cache.setdefault(group_id, {})
                names[int(topic_id)] = real_title
            return True
        def is_generic_topic_title(title, topic_id):
            title = (title or "").strip()
            return not title or title == f"Topic {topic_id}"
        def route_titles_compatible(route):
            if route.get("accepted"):
                return True
            src = (route.get("src_topic_title") or "").strip().lower()
            dst = (route.get("dest_topic_title") or "").strip().lower()
            if not src or not dst:
                return True
            if int(route.get("src_topic_id", 1) or 1) == 1:
                return True
            simple = lambda s: re.sub(r"[^a-z0-9]+", " ", s.replace("t'au", "tau")).strip()
            src_s = simple(src)
            dst_s = simple(dst)
            if src_s == dst_s or src_s in dst_s or dst_s in src_s:
                return True
            allowed = [
                ("admech", "adeptus mechanicus"),
                ("battle sisters", "adeptus sororitas"),
                ("gsc", "genestealer cults"),
                ("dark eldar", "drukhari"),
                ("lotr", "lord of the rings"),
                ("literature", "books"),
                ("bfg", "battlefleet gothic"),
                ("grey knight", "grey knights"),
                ("aos", "age of sigmar"),
                ("league of votann", "league of votann"),
                ("tau empire", "tau"),
                ("t au empire", "tau"),
                ("imperial chaos knights", "imperial knights"),
                ("thousand sons tzeentch deamons", "thousand sons"),
                ("emperor childrens slaanesh deamons", "emperors children"),
                ("world eaters khorne deamons", "world eaters"),
                ("death guard nurgle deamons", "death guard"),
                ("warhammer old world", "the old world"),
            ]
            if any(a in src_s and b in dst_s for a, b in allowed):
                return True
            return difflib.SequenceMatcher(None, src_s, dst_s).ratio() >= 0.42
        async def enrich_route_names(client, route, src_forum, dest_topic_id):
            src_topic_id = route.get("src_topic_id", 1)
            if src_forum:
                names = await topic_names(client, route["src_group_id"])
                route["src_topic_title"] = names.get(src_topic_id, route.get("src_topic_title") or f"Topic {src_topic_id}")
            elif is_generic_topic_title(route.get("src_topic_title"), src_topic_id):
                route["src_topic_title"] = "General"
            if dest_topic_id:
                names = await topic_names(client, route["dest_group_id"])
                route["dest_topic_title"] = names.get(dest_topic_id, route.get("dest_topic_title") or f"Topic {dest_topic_id}")
            if not route.get("src_group_name"):
                route["src_group_name"] = await group_name(client, route["src_group_id"])
            if not route.get("dest_group_name"):
                route["dest_group_name"] = await group_name(client, route["dest_group_id"])
            if not route.get("src_group_name"):
                route["src_group_name"] = str(route.get("src_group_id", "Unknown"))
            if not route.get("dest_group_name"):
                route["dest_group_name"] = str(route.get("dest_group_id", "Unknown"))
        async def count_iter_messages(client, group_id, min_id, src_forum, src_topic_id):
            count = 0
            if src_forum:
                msg_iter = client.iter_messages(group_id, reverse=True, min_id=min_id, reply_to=src_topic_id)
            else:
                msg_iter = client.iter_messages(group_id, reverse=True, min_id=min_id)
            async for _ in msg_iter:
                if stop_event.is_set():
                    break
                count += 1
            return count
        async def scan_dest_topic_files(client, group_id, topic_id, topic_title):
            group_files = dest_files.setdefault(str(group_id), {})
            dest_index = dest_index_for(group_id)
            group_scan = dest_scan.setdefault(str(group_id), {})
            scan_info = group_scan.setdefault(str(topic_id), {"last_msg_id": 0})
            previous_count = int(scan_info.get("file_count", 0) or 0)
            topic_files = {}
            indexed_files = {}
            scanned = 0
            highest_seen = 0
            log_q.put(f"Scanning destination topic: {topic_title} - rebuilding this topic's live file index from Telegram")
            msg_iter = client.iter_messages(group_id, reverse=True, reply_to=topic_id)
            async for msg in msg_iter:
                if stop_event.is_set():
                    break
                highest_seen = max(highest_seen, int(getattr(msg, "id", 0) or 0))
                media = getattr(msg, "media", None)
                fn = _get_fn(media) if media else None
                sz = _get_sz(media) if media else None
                if fn and sz:
                    topic_files[fn] = sz
                    row = indexed_files.setdefault(fn, {"size": int(sz), "sizes": [], "msg_id": None})
                    row["size"] = int(sz)
                    if int(sz) not in row["sizes"]:
                        row["sizes"].append(int(sz))
                    row["msg_id"] = getattr(msg, "id", None)
                    scanned += 1
                    if scanned % 500 == 0:
                        log_q.put(f"  scanned {scanned} file(s) in {topic_title}...")
            group_files[str(topic_id)] = topic_files
            _dest_index_replace_topic(dest_index, topic_id, indexed_files)
            scan_info["last_msg_id"] = highest_seen
            scan_info["file_count"] = len(topic_files)
            save_state()
            save_dest_index_for(group_id)
            delta = len(topic_files) - previous_count
            log_q.put(f"Scanned destination topic: {topic_title} - {len(topic_files)} live file(s), index adjusted by {delta}")
            return group_files
        async def live_dest_topic_files(client, group_id, topic_id, topic_title):
            live_files = {}
            live_file_keys = set()
            indexed_files = {}
            dest_index = dest_index_for(group_id)
            scanned = 0
            log_q.put(f"Repair files: rebuilding live destination index for {topic_title}")
            async for msg in client.iter_messages(group_id, reverse=True, reply_to=topic_id):
                if stop_event.is_set():
                    break
                media = getattr(msg, "media", None)
                fn = _get_fn(media) if media else None
                sz = _get_sz(media) if media else None
                if fn and sz:
                    live_file_keys.add((str(fn).strip().casefold(), int(sz or 0)))
                    live_files[fn] = sz
                    row = indexed_files.setdefault(fn, {"size": int(sz), "sizes": [], "msg_id": None})
                    row["size"] = int(sz)
                    if int(sz) not in row["sizes"]:
                        row["sizes"].append(int(sz))
                    row["msg_id"] = getattr(msg, "id", None)
                    scanned += 1
                    if scanned % 500 == 0:
                        log_q.put(f"  scanned {scanned} destination file(s) in {topic_title}...")
            _dest_index_replace_topic(dest_index, topic_id, indexed_files)
            dest_files.setdefault(str(group_id), {})[str(topic_id)] = live_files
            save_state()
            save_dest_index_for(group_id)
            log_q.put(f"Repair files: destination has {len(live_files)} live file(s) in {topic_title}")
            return live_files, live_file_keys
        async def collect_link_repairs(client, route, src_forum, src_topic_id, last_id):
            if not last_id:
                return []
            repairs = []
            if src_forum:
                msg_iter = client.iter_messages(route["src_group_id"], reverse=True, max_id=last_id + 1, reply_to=src_topic_id)
            else:
                msg_iter = client.iter_messages(route["src_group_id"], reverse=True, max_id=last_id + 1)
            async for msg in msg_iter:
                if stop_event.is_set():
                    break
                if not _message_needs_link_repair(msg):
                    continue
                media = msg.media
                old_text = msg.message or ""
                if isinstance(media, MessageMediaWebPage):
                    media = None
                kind = _media_kind(media)
                if kind == "text" and not filters.get("messages", True):
                    continue
                if kind == "photo" and not filters.get("images", True):
                    continue
                if kind == "document" and not filters.get("files", True):
                    continue
                repairs.append({
                    "msg": msg,
                    "media": media,
                    "old_text": old_text,
                    "new_text": _message_text_with_links(msg),
                    "kind": kind,
                    "fn": _get_fn(media) if media else None,
                    "sz": _get_sz(media) if media else None,
                })
            return repairs
        def repair_matches_dest(target, dest_msg):
            dest_media = dest_msg.media
            if isinstance(dest_media, MessageMediaWebPage):
                dest_media = None
            dest_text = dest_msg.message or ""
            if dest_text != target["old_text"]:
                return False
            if "Links:" in dest_text:
                return False
            if _media_kind(dest_media) != target["kind"]:
                return False
            if target["kind"] == "document":
                return _get_fn(dest_media) == target["fn"] and _get_sz(dest_media) == target["sz"]
            if target["kind"] == "photo":
                return bool(target["old_text"].strip())
            if target["kind"] == "text":
                return bool(target["old_text"].strip())
            return False
        async def repair_route_links(client, route, dest_topic_id, src_forum, src_topic_id, ms):
            route_label = f"{route.get('src_group_name','Unknown')} / {route.get('src_topic_title','General')} -> {route.get('dest_group_name','Unknown')} / {route.get('dest_topic_title','Unknown')}"
            repairs = await collect_link_repairs(client, route, src_forum, src_topic_id, int(ms.get("last_msg_id", 0) or 0))
            if not repairs:
                log_q.put(f"Repair links: no missing-link messages found in {route_label}")
                return 0, 0, 0
            log_q.put(f"Repair links: {len(repairs)} message(s) need links in {route_label}")
            pending = list(repairs)
            fixed = skipped = errors = scanned = 0
            dest_iter = client.iter_messages(route["dest_group_id"], reverse=False, reply_to=dest_topic_id)
            async for dest_msg in dest_iter:
                if stop_event.is_set() or not pending:
                    break
                scanned += 1
                if scanned % 500 == 0:
                    log_q.put(f"Repair links: scanned {scanned} destination message(s) in {route.get('dest_topic_title','Unknown')}")
                match_i = next((i for i, target in enumerate(pending) if repair_matches_dest(target, dest_msg)), None)
                if match_i is None:
                    continue
                target = pending.pop(match_i)
                try:
                    kwargs = {"reply_to": dest_topic_id}
                    if target["media"]:
                        await safe(lambda t=target: client.send_file(route["dest_group_id"], t["media"], caption=t["new_text"], **kwargs))
                    elif target["new_text"]:
                        await safe(lambda t=target: client.send_message(route["dest_group_id"], t["new_text"], **kwargs))
                    await safe(lambda mid=dest_msg.id: client.delete_messages(route["dest_group_id"], [mid]))
                    fixed += 1
                    label = target["fn"] or f"message {target['msg'].id}"
                    log_q.put(f"Repair links: fixed {label}")
                    await asyncio.sleep(1)
                except InterruptedError:
                    raise
                except Exception as e:
                    errors += 1
                    log_q.put(f"ERROR repair links [{route_label}] msg {target['msg'].id}: {e}")
            for target in pending:
                skipped += 1
                label = target["fn"] or f"message {target['msg'].id}"
                if not target["old_text"].strip() and target["kind"] in ("photo", "text"):
                    log_q.put(f"Repair links: manual check needed for {label} - old copy has no text, so it cannot be matched safely")
                else:
                    log_q.put(f"Repair links: old copy not found for {label}")
            log_q.put(f"Repair links done: {fixed} fixed, {skipped} skipped, {errors} errors in {route_label}")
            return fixed, skipped, errors
        async def repair_route_missing_files(client, route, dest_topic_id, src_forum, src_topic_id, ms):
            from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, MessageMediaWebPage
            route_label = f"{route.get('src_group_name','Unknown')} / {route.get('src_topic_title','General')} -> {route.get('dest_group_name','Unknown')} / {route.get('dest_topic_title','Unknown')}"
            dest_index = dest_index_for(route["dest_group_id"])
            existing_files, existing_file_keys = await live_dest_topic_files(
                client, route["dest_group_id"], dest_topic_id,
                route.get("dest_topic_title") or f"Topic {dest_topic_id}"
            )
            copied = skipped = errors = scanned = missing = 0
            sent_files = ms.setdefault("sent_files", {})
            repair_done = state.setdefault("__repair_missing_files", {})
            repair_route_key = f"{route['src_group_id']}_{src_topic_id}_{route['dest_group_id']}_{dest_topic_id}"
            repaired_sources = repair_done.setdefault(repair_route_key, {})
            repair_done_by_source = state.setdefault("__repair_missing_files_by_source", {})
            def repair_source_key(msg, fn, sz):
                return f"{getattr(msg, 'id', 0)}|{fn}|{int(sz or 0)}"
            def repair_global_source_key(msg, fn, sz):
                return f"{route['src_group_id']}_{src_topic_id}_{getattr(msg, 'id', 0)}|{fn}|{int(sz or 0)}"
            def file_already_exists(fn, sz):
                return (str(fn).strip().casefold(), int(sz or 0)) in existing_file_keys
            def remember_existing_file(fn, sz):
                existing_files[fn] = sz
                existing_file_keys.add((str(fn).strip().casefold(), int(sz or 0)))
            def mark_repaired(msg, fn, sz):
                repaired_sources[repair_source_key(msg, fn, sz)] = True
                repair_done_by_source[repair_global_source_key(msg, fn, sz)] = {
                    "dest_group_id": route["dest_group_id"],
                    "dest_topic_id": dest_topic_id,
                    "dest_topic_title": route.get("dest_topic_title", ""),
                }
            before_file_images = []
            def reply_to_msg_id(message):
                reply = getattr(message, "reply_to", None)
                return getattr(reply, "reply_to_msg_id", None) if reply else None
            def close_to_file(image_record, file_msg):
                image_date = image_record.get("date")
                file_date = getattr(file_msg, "date", None)
                if not image_date or not file_date:
                    return False
                return abs((file_date - image_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
            async def send_preview_image(record):
                nonlocal copied, skipped, errors
                try:
                    if filters.get("convert_image_files", False) and _is_image_document(record["media"]) and record.get("message"):
                        data = await client.download_media(record["message"], file=bytes)
                        name = _get_fn(record["media"]) or "image.jpg"
                        async def send_as_photo():
                            bio = io.BytesIO(data)
                            bio.name = name
                            return await client.send_file(
                                route["dest_group_id"], bio, caption=record["text"], reply_to=dest_topic_id,
                                force_document=False)
                        try:
                            await send_as_photo()
                        except Exception as photo_err:
                            if not _photo_upload_too_large_error(photo_err):
                                raise
                            log_q.put(f"WARN repair files [{route_label}] image too large as photo; sending as file instead")
                            await client.send_file(
                                route["dest_group_id"], record["media"], caption=record["text"], reply_to=dest_topic_id,
                                force_document=True)
                    else:
                        await client.send_file(
                            route["dest_group_id"], record["media"], caption=record["text"], reply_to=dest_topic_id,
                            force_document=False)
                    copied += 1
                    return True
                except InterruptedError:
                    raise
                except Exception as img_err:
                    skipped += 1
                    log_q.put(f"WARN repair files preview [{route_label}] skipped msg {record['id']}: {img_err}")
                    return False
            def skip_buffered_previews():
                nonlocal skipped, before_file_images
                if before_file_images:
                    skipped += len(before_file_images)
                    before_file_images = []
            log_q.put(f"Repair files: scanning source {route_label}")
            if src_forum:
                msg_iter = client.iter_messages(route["src_group_id"], reverse=True, reply_to=src_topic_id)
            else:
                msg_iter = client.iter_messages(route["src_group_id"], reverse=True)
            async for msg in msg_iter:
                if stop_event.is_set():
                    log_q.put("Repair files stopped.")
                    break
                scanned += 1
                try:
                    media = msg.media
                    text = _message_text_with_links(msg)
                    if isinstance(media, MessageMediaWebPage):
                        media = None
                    noise_media = media and _is_standalone_decoration_media(media)
                    if noise_media:
                        skipped += 1
                        continue
                    is_photo = isinstance(media, MessageMediaPhoto) or _is_image_document(media)
                    is_file = isinstance(media, MessageMediaDocument) and not _is_image_document(media)
                    if is_photo:
                        if filters.get("images", True):
                            before_file_images.append({"id": msg.id, "media": media, "text": text, "date": msg.date, "message": msg})
                            if len(before_file_images) > PREVIEW_IMAGE_LIMIT:
                                before_file_images.pop(0)
                                skipped += 1
                        else:
                            skipped += 1
                        continue
                    if not is_file:
                        continue
                    fn = _get_fn(media)
                    sz = _get_sz(media)
                    if not fn or not sz:
                        skipped += 1
                        skip_buffered_previews()
                        continue
                    src_repair_key = repair_source_key(msg, fn, sz)
                    global_repair_key = repair_global_source_key(msg, fn, sz)
                    if repaired_sources.get(src_repair_key) or repair_done_by_source.get(global_repair_key):
                        skipped += 1
                        skip_buffered_previews()
                        continue
                    if file_already_exists(fn, sz):
                        mark_repaired(msg, fn, sz)
                        save_state()
                        skipped += 1
                        skip_buffered_previews()
                        continue
                    missing += 1
                    kwargs = {"reply_to": dest_topic_id}
                    await safe(lambda: client.send_file(route["dest_group_id"], media, caption=text, **kwargs))
                    remember_existing_file(fn, sz)
                    _track(sent_files, dest_topic_id, fn, sz)
                    ms["sent_files"] = sent_files
                    _dest_index_track(dest_index, dest_topic_id, fn, sz)
                    mark_repaired(msg, fn, sz)
                    save_state()
                    save_dest_index_for(route["dest_group_id"])
                    copied += 1
                    file_reply_id = reply_to_msg_id(msg)
                    selected_previews = [
                        preview for preview in before_file_images
                        if preview["id"] == file_reply_id or close_to_file(preview, msg)
                    ][-PREVIEW_IMAGE_LIMIT:]
                    skipped += max(0, len(before_file_images) - len(selected_previews))
                    for preview in selected_previews:
                        await send_preview_image(preview)
                    before_file_images = []
                    log_q.put(f"Repair files: copied missing file [{route.get('dest_topic_title','Unknown')}] {fn}")
                    if missing % 10 == 0:
                        log_q.put(f"Repair files progress: {missing} missing file(s) copied in {route_label} | {scanned} source messages scanned")
                    await asyncio.sleep(1)
                except InterruptedError:
                    raise
                except Exception as e:
                    errors += 1
                    skip_buffered_previews()
                    log_q.put(f"ERROR repair files [{route_label}] msg {getattr(msg, 'id', '?')}: {e}")
            skip_buffered_previews()
            log_q.put(f"Repair files done: {missing} missing file(s), {copied} copied items, {skipped} skipped, {errors} errors in {route_label}")
            return missing, copied, skipped, errors
        async def ensure_dest_topic(client, route):
            dest_id = route.get("dest_topic_id")
            title = route.get("dest_topic_title", "").strip()
            dst_entity = None
            if dest_id:
                if int(dest_id) == 1:
                    return 1
                names = await topic_names(client, route["dest_group_id"])
                if int(dest_id) in names:
                    return int(dest_id)
                if await verify_topic_id(client, route["dest_group_id"], int(dest_id), title):
                    return int(dest_id)
                if title:
                    matches = [tid for tid, name in names.items() if name.strip().lower() == title.lower()]
                    if len(matches) == 1:
                        new_id = int(matches[0])
                        old_id = dest_id
                        route["dest_topic_id"] = new_id
                        save_state()
                        log_q.put(f"WARN destination topic id changed for {title}: {old_id} -> {new_id}")
                        return new_id
                    if len(matches) > 1:
                        raise RuntimeError(f"Destination topic id {dest_id} no longer exists and multiple live topics are named '{title}'. Fix this route manually.")
                raise RuntimeError(f"Destination topic id {dest_id} no longer exists for '{title or route.get('src_topic_title','Unknown')}'. Route skipped to avoid sending to General.")
            if not title:
                raise RuntimeError(f"Route has no destination topic: {route.get('src_group_name','Unknown')} / {route.get('src_topic_title','Unknown')}")
            dst_entity = await client.get_entity(route["dest_group_id"])
            if not getattr(dst_entity, "forum", False):
                raise RuntimeError(f"Destination group '{route.get('dest_group_name','Unknown')}' does not have topics enabled, so '{title}' cannot be created safely.")
            group_key = str(route["dest_group_id"])
            created_topics.setdefault(group_key, {})
            if title in created_topics[group_key]:
                return created_topics[group_key][title]
            dst = await client.get_input_entity(route["dest_group_id"])
            log_q.put(f"Creating destination topic: {title}")
            new = await safe(lambda t=title: client(CreateForumTopicRequest(peer=dst, title=t)))
            nid = new.updates[0].id
            created_topics[group_key][title] = nid
            route["dest_topic_id"] = nid
            save_state()
            log_q.put(f"  -> dest id {nid}")
            return nid
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); log_q.put("__DONE__"); return
            prepared = []
            total_remaining = 0
            scanned_dest_keys = set()
            route_count = len(routes)
            log_q.put(f"Preparing routes... 0/{route_count}")
            for route_index, route in enumerate(routes, 1):
                if stop_event.is_set(): break
                if route_index == 1 or route_index % 5 == 0 or route_index == route_count:
                    label = (
                        f"{route.get('src_group_name','Unknown')} / {route.get('src_topic_title','Unknown')} -> "
                        f"{route.get('dest_group_name','Unknown')} / {route.get('dest_topic_title','Unknown')}"
                    )
                    log_q.put(f"Preparing route {route_index}/{route_count}: {label}")
                try:
                    dest_topic_id = await ensure_dest_topic(client, route)
                except Exception as e:
                    log_q.put(
                        f"ERROR skipped route: {route.get('src_group_name','Unknown')} / "
                        f"{route.get('src_topic_title','Unknown')} -> {route.get('dest_group_name','Unknown')} / "
                        f"{route.get('dest_topic_title','Unknown')}: {e}"
                    )
                    continue
                src_topic_id = route.get("src_topic_id", 1)
                src_forum = route.get("source_has_topics")
                if src_forum is None:
                    src_forum = await source_has_topics(client, route["src_group_id"])
                await enrich_route_names(client, route, src_forum, dest_topic_id)
                if not route_titles_compatible(route):
                    log_q.put(
                        f"ERROR blocked unsafe route: {route.get('src_group_name','Unknown')} / "
                        f"{route.get('src_topic_title','Unknown')} -> {route.get('dest_group_name','Unknown')} / "
                        f"{route.get('dest_topic_title','Unknown')}"
                    )
                    continue
                if filters.get("skip_duplicates", True) and filters.get("scan_destination", False):
                    dest_key = f"{route['dest_group_id']}_{dest_topic_id}"
                    if dest_key not in scanned_dest_keys:
                        await scan_dest_topic_files(
                            client, route["dest_group_id"], dest_topic_id,
                            route.get("dest_topic_title") or f"Topic {dest_topic_id}"
                        )
                        scanned_dest_keys.add(dest_key)
                key = f"{route['src_group_id']}_{src_topic_id}_{route['dest_group_id']}_{dest_topic_id}"
                ms = state.setdefault(key, {"last_msg_id":0,"sent_files":{}})
                last_id = ms.get("last_msg_id", 0)
                route_total = await count_iter_messages(client, route["src_group_id"], last_id, src_forum, src_topic_id)
                if route_index == 1 or route_index % 5 == 0 or route_index == route_count:
                    log_q.put(f"  Prepared {route_index}/{route_count}: {route_total} message(s) pending")
                total_remaining += route_total
                prepared.append((route, dest_topic_id, src_forum, key, ms, route_total))
            if repair_links:
                log_q.put("Repair Missing Links mode: checking messages already copied by this project.\n")
                fixed_total = skipped_total = errors_total = 0
                for route, dest_topic_id, src_forum, key, ms, route_total in prepared:
                    if stop_event.is_set():
                        log_q.put("Stopped between routes.")
                        break
                    f, s, e = await repair_route_links(
                        client, route, dest_topic_id, src_forum,
                        route.get("src_topic_id", 1), ms
                    )
                    fixed_total += f; skipped_total += s; errors_total += e
                log_q.put(f"\nRepair complete. {fixed_total} fixed, {skipped_total} skipped, {errors_total} errors.")
                return
            if repair_missing_files:
                log_q.put("Repair Missing Files mode: repairing missing links first, then scanning old source history and live destination topics. Normal project progress will not be changed.\n")
                missing_total = copied_total = skipped_total = errors_total = 0
                link_fixed_total = link_skipped_total = link_errors_total = 0
                for route, dest_topic_id, src_forum, key, ms, route_total in prepared:
                    if stop_event.is_set():
                        log_q.put("Stopped between routes.")
                        break
                    f, ls, le = await repair_route_links(
                        client, route, dest_topic_id, src_forum,
                        route.get("src_topic_id", 1), ms
                    )
                    link_fixed_total += f; link_skipped_total += ls; link_errors_total += le
                    if stop_event.is_set():
                        log_q.put("Stopped between link repair and file repair.")
                        break
                    m, c, s, e = await repair_route_missing_files(
                        client, route, dest_topic_id, src_forum,
                        route.get("src_topic_id", 1), ms
                    )
                    missing_total += m; copied_total += c; skipped_total += s; errors_total += e
                log_q.put(
                    f"\nSUMMARY Repair files complete. Links: {link_fixed_total} fixed, "
                    f"{link_skipped_total} skipped, {link_errors_total} errors. "
                    f"Files: {missing_total} missing file(s), {copied_total} copied items, "
                    f"{skipped_total} skipped, {errors_total} errors."
                )
                return
            log_q.put(f"Total messages to copy across all routes: {total_remaining}\n")
            copied = skipped = errors = processed_total = 0
            for route, dest_topic_id, src_forum, key, ms, route_total in prepared:
                if stop_event.is_set():
                    log_q.put("Stopped between routes.")
                    break
                src_topic_id = route.get("src_topic_id", 1)
                last_id = ms.get("last_msg_id", 0)
                sent_files = ms.setdefault("sent_files", {})
                destination_sent_files = dest_files.setdefault(str(route["dest_group_id"]), {})
                dest_index = dest_index_for(route["dest_group_id"])
                if _dest_index_import_sent_files(dest_index, destination_sent_files):
                    save_dest_index_for(route["dest_group_id"])
                route_label = f"{route.get('src_group_name','Unknown')} / {route.get('src_topic_title','General')} -> {route.get('dest_group_name','Unknown')} / {route.get('dest_topic_title','Unknown')}"
                progress_label = route_label.replace(" -> ", " -> ")
                log_q.put(f"\nRoute: {route_label}")
                log_q.put(f"   Messages to copy this topic: {route_total} | resuming from msg {last_id}")
                if src_forum:
                    msg_iter = client.iter_messages(route["src_group_id"], reverse=True, min_id=last_id, reply_to=src_topic_id)
                else:
                    msg_iter = client.iter_messages(route["src_group_id"], reverse=True, min_id=last_id)
                processed_route = 0
                route_copied = route_skipped = route_errors = 0
                before_file_images = []
                after_file_image_slots = 0
                after_file_anchor_date = None
                def reply_to_msg_id(message):
                    reply = getattr(message, "reply_to", None)
                    return getattr(reply, "reply_to_msg_id", None) if reply else None
                def close_to_file(image_record, file_msg):
                    image_date = image_record.get("date")
                    file_date = getattr(file_msg, "date", None)
                    if not image_date or not file_date:
                        return False
                    return abs((file_date - image_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
                def close_to_last_file(message):
                    if not after_file_anchor_date or not getattr(message, "date", None):
                        return False
                    return abs((message.date - after_file_anchor_date).total_seconds()) <= PREVIEW_IMAGE_WINDOW_SECONDS
                async def send_preview_image(record):
                    nonlocal copied, route_copied, skipped, route_skipped, errors, route_errors
                    try:
                        if filters.get("convert_image_files", False) and _is_image_document(record["media"]) and record.get("message"):
                            data = await client.download_media(record["message"], file=bytes)
                            name = _get_fn(record["media"]) or "image.jpg"
                            async def send_as_photo():
                                bio = io.BytesIO(data)
                                bio.name = name
                                return await client.send_file(
                                    route["dest_group_id"], bio, caption=record["text"], reply_to=dest_topic_id,
                                    force_document=False)
                            try:
                                await send_as_photo()
                            except Exception as photo_err:
                                if not _photo_upload_too_large_error(photo_err):
                                    raise
                                log_q.put(f"WARN [{route_label}] image too large as photo; sending as file instead")
                                await client.send_file(
                                    route["dest_group_id"], record["media"], caption=record["text"], reply_to=dest_topic_id,
                                    force_document=True)
                        else:
                            await client.send_file(
                                route["dest_group_id"], record["media"], caption=record["text"], reply_to=dest_topic_id,
                                force_document=False)
                        copied += 1; route_copied += 1
                        return True
                    except InterruptedError:
                        raise
                    except Exception as img_err:
                        skipped += 1; route_skipped += 1
                        log_q.put(f"WARN [{route_label}] skipped preview msg {record['id']}: {img_err}")
                        return False
                def skip_buffered_previews():
                    nonlocal skipped, route_skipped, before_file_images
                    if before_file_images:
                        skipped += len(before_file_images)
                        route_skipped += len(before_file_images)
                        before_file_images = []
                async for msg in msg_iter:
                    if stop_event.is_set():
                        log_q.put(f"Stopped at message {ms.get('last_msg_id', 0)}."); break
                    try:
                        from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                        media = msg.media; text = _message_text_with_links(msg)
                        if isinstance(media, MessageMediaWebPage): media = None
                        noise_media = media and _is_standalone_decoration_media(media)
                        if noise_media and text:
                            media = None
                            noise_media = False
                        is_photo = isinstance(media, MessageMediaPhoto) or _is_image_document(media)
                        is_file  = isinstance(media, MessageMediaDocument) and not _is_image_document(media)
                        is_text  = not media and bool(text)
                        skip_this = False
                        if noise_media: skip_this = True
                        if is_text  and not filters.get("messages", True): skip_this = True
                        if is_photo and not filters.get("images",   True): skip_this = True
                        if is_file  and not filters.get("files",    True): skip_this = True
                        if skip_this:
                            skipped += 1; route_skipped += 1
                        else:
                            kwargs = {"reply_to": dest_topic_id}
                            if is_photo:
                                record = {"id": msg.id, "media": media, "text": text, "date": msg.date, "message": msg}
                                if after_file_image_slots > 0 and close_to_last_file(msg):
                                    await send_preview_image(record)
                                    after_file_image_slots -= 1
                                else:
                                    if after_file_image_slots > 0:
                                        after_file_image_slots = 0
                                    before_file_images.append(record)
                                    if len(before_file_images) > PREVIEW_IMAGE_LIMIT:
                                        before_file_images.pop(0)
                                        skipped += 1; route_skipped += 1
                            elif media:
                                fn = _get_fn(media); sz = _get_sz(media)
                                dup_in_route = _is_dup(sent_files, dest_topic_id, fn, sz)
                                dup_in_dest = _is_dup(destination_sent_files, dest_topic_id, fn, sz)
                                dup_in_index = _dest_index_has(dest_index, dest_topic_id, fn, sz)
                                if filters.get("skip_duplicates", True) and (dup_in_route or dup_in_dest or dup_in_index):
                                    skipped += 1; route_skipped += 1
                                    skip_buffered_previews()
                                    after_file_image_slots = 0
                                else:
                                    after_file_image_slots = 0
                                    await safe(lambda: client.send_file(route["dest_group_id"], media, caption=text, **kwargs))
                                    _track(sent_files, dest_topic_id, fn, sz)
                                    _track(destination_sent_files, dest_topic_id, fn, sz)
                                    _dest_index_track(dest_index, dest_topic_id, fn, sz)
                                    save_dest_index_for(route["dest_group_id"])
                                    copied += 1; route_copied += 1
                                    file_reply_id = reply_to_msg_id(msg)
                                    selected_previews = [
                                        preview for preview in before_file_images
                                        if preview["id"] == file_reply_id or close_to_file(preview, msg)
                                    ][-PREVIEW_IMAGE_LIMIT:]
                                    skipped_previews = len(before_file_images) - len(selected_previews)
                                    if skipped_previews > 0:
                                        skipped += skipped_previews; route_skipped += skipped_previews
                                    for preview in selected_previews:
                                        await send_preview_image(preview)
                                    before_file_images = []
                                    after_file_anchor_date = msg.date
                                    after_file_image_slots = max(0, PREVIEW_IMAGE_LIMIT - len(selected_previews)) if filters.get("images", True) else 0
                            elif text:
                                await safe(lambda: client.send_message(route["dest_group_id"], text, **kwargs))
                                copied += 1; route_copied += 1
                        # Always save progress — even for skipped messages
                        ms["last_msg_id"] = msg.id; ms["sent_files"] = sent_files
                        save_state()
                        processed_route += 1; processed_total += 1
                        report_every = max(1, min(10, route_total // 20)) if route_total else 10
                        if processed_route % report_every == 0 or processed_route == route_total:
                            shown_route_total = max(route_total, processed_route)
                            shown_total_remaining = max(total_remaining, processed_total)
                            rpct = min(100.0, round((processed_route/shown_route_total)*100,1)) if shown_route_total else 100.0
                            tpct = min(100.0, round((processed_total/shown_total_remaining)*100,1)) if shown_total_remaining else 100.0
                            log_q.put(f"Progress: [{progress_label}] "
                                      f"{processed_route}/{shown_route_total} this topic ({rpct}%) | "
                                      f"{route_skipped} skipped, {route_errors} errors | "
                                      f"{processed_total}/{shown_total_remaining} this run ({tpct}%)")
                        await asyncio.sleep(1)
                    except InterruptedError: break
                    except Exception as e:
                        if _is_no_retry_send_error(e):
                            errors += 1; route_errors += 1
                            skip_buffered_previews()
                            after_file_image_slots = 0
                            ms["last_msg_id"] = msg.id; ms["sent_files"] = sent_files
                            save_state()
                            processed_route += 1; processed_total += 1
                            log_q.put(f"ERROR [{route_label}] msg {msg.id}: {_send_error_label(e)}")
                            report_every = max(1, min(10, route_total // 20)) if route_total else 10
                            if processed_route % report_every == 0 or processed_route == route_total:
                                shown_route_total = max(route_total, processed_route)
                                shown_total_remaining = max(total_remaining, processed_total)
                                rpct = min(100.0, round((processed_route/shown_route_total)*100,1)) if shown_route_total else 100.0
                                tpct = min(100.0, round((processed_total/shown_total_remaining)*100,1)) if shown_total_remaining else 100.0
                                log_q.put(f"Progress: [{progress_label}] "
                                          f"{processed_route}/{shown_route_total} this topic ({rpct}%) | "
                                          f"{route_skipped} skipped, {route_errors} errors | "
                                          f"{processed_total}/{shown_total_remaining} this run ({tpct}%)")
                        else:
                            errors += 1; route_errors += 1
                            log_q.put(f"ERROR [{route_label}] msg {msg.id}: {e}")
                skip_buffered_previews()
            save_state()
            log_q.put(f"\nDone. {copied} copied, {skipped} skipped, {errors} errors across {len(prepared)} route(s).")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")
    _new_loop(_r())
def scan_clean_duplicates(api_id, api_hash, group_id, topics, selected_ids, include_preview_images, log_q, result_q, stop_event):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, MessageMediaWebPage
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); result_q.put([]); return
        selected = set(int(x) for x in selected_ids)
        topic_rows = [t for t in topics if int(t["id"]) in selected]
        client = TelegramClient("session_gui", api_id, api_hash)
        duplicates = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); result_q.put([]); return
            entity = await client.get_entity(group_id)
            is_forum = bool(getattr(entity, "forum", False))
            log_q.put(f"Clean scan: {len(topic_rows)} topic(s), duplicate files, previews={'yes' if include_preview_images else 'no'}")
            for topic in topic_rows:
                if stop_event.is_set():
                    break
                tid = int(topic["id"])
                title = topic.get("title") or "General"
                seen = {}
                rows = []
                scanned = 0
                log_q.put(f"Clean scan topic: {title}")
                if is_forum:
                    msg_iter = client.iter_messages(group_id, reverse=True, reply_to=tid)
                else:
                    msg_iter = client.iter_messages(group_id, reverse=True)
                async for msg in msg_iter:
                    if stop_event.is_set():
                        break
                    media = msg.media
                    if isinstance(media, MessageMediaWebPage) or not media:
                        continue
                    scanned += 1
                    row = {"msg_id": msg.id, "kind": "other", "sig": None, "label": "", "size": None}
                    if isinstance(media, MessageMediaDocument) and _is_download_file(media):
                        fn = _get_fn(media) or f"file msg {msg.id}"
                        size = _get_sz(media)
                        if fn and size:
                            row = {"msg_id": msg.id, "kind": "file", "sig": (fn.lower(), int(size)), "label": fn, "size": int(size)}
                    elif isinstance(media, MessageMediaPhoto):
                        row = {"msg_id": msg.id, "kind": "image", "sig": None, "label": (msg.message or "").strip()[:80] or f"image msg {msg.id}", "size": None}
                    rows.append(row)
                    if row["kind"] != "file":
                        continue
                    if row["sig"] not in seen:
                        seen[row["sig"]] = row
                    else:
                        duplicates.append({
                            "topic_id": tid,
                            "topic_title": title,
                            "msg_id": row["msg_id"],
                            "keep_msg_id": seen[row["sig"]]["msg_id"],
                            "kind": "file",
                            "label": row["label"],
                            "size": row["size"],
                            "preview_msg_ids": [],
                            "delete_msg_ids": [row["msg_id"]],
                        })
                    if scanned % 250 == 0:
                        log_q.put(f"  scanned {scanned} media message(s) in {title}...")
                if include_preview_images:
                    by_msg_id = {row["msg_id"]: i for i, row in enumerate(rows)}
                    for dup in [d for d in duplicates if d["topic_id"] == tid]:
                        idx = by_msg_id.get(dup["msg_id"])
                        if idx is None:
                            continue
                        preview_ids = []
                        for direction in (-1, 1):
                            steps = 0
                            j = idx + direction
                            while 0 <= j < len(rows) and steps < 6:
                                steps += 1
                                if rows[j]["kind"] == "file":
                                    break
                                if rows[j]["kind"] == "image":
                                    preview_ids.append(rows[j]["msg_id"])
                                j += direction
                        preview_ids = sorted(set(preview_ids))
                        dup["preview_msg_ids"] = preview_ids
                        dup["delete_msg_ids"] = [dup["msg_id"]] + preview_ids
            log_q.put(f"Clean scan complete: {len(duplicates)} duplicate item(s) found.")
            result_q.put(duplicates)
        except Exception as e:
            log_q.put(f"Clean scan error: {e}")
            result_q.put([])
        finally:
            await client.disconnect()
    _new_loop(_r())
def delete_clean_duplicates(api_id, api_hash, group_id, msg_ids, log_q, result_q, stop_event):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); result_q.put(False); return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); result_q.put(False); return
            ids = [int(x) for x in msg_ids]
            deleted = 0
            for i in range(0, len(ids), 100):
                if stop_event.is_set():
                    break
                batch = ids[i:i + 100]
                await client.delete_messages(group_id, batch)
                deleted += len(batch)
                log_q.put(f"Clean delete: deleted {deleted}/{len(ids)} duplicate message(s)")
                await asyncio.sleep(1)
            log_q.put(f"Clean delete complete: {deleted} message(s) deleted.")
            result_q.put(True)
        except Exception as e:
            log_q.put(f"Clean delete error: {e}")
            result_q.put(False)
        finally:
            await client.disconnect()
    _new_loop(_r())
def calculate_download_topics(api_id, api_hash, source_id, topics, selected_ids, filters, log_q, result_q, stop_event):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, MessageMediaWebPage
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); result_q.put(None); return
        selected = set(int(x) for x in selected_ids)
        topic_rows = [t for t in topics if int(t["id"]) in selected]
        client = TelegramClient("session_gui", api_id, api_hash)
        rows = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); result_q.put(None); return
            src = await client.get_entity(source_id)
            has_topics = bool(getattr(src, "forum", False))
            for topic in topic_rows:
                if stop_event.is_set(): break
                tid = int(topic["id"])
                title = topic.get("title", "General")
                count = 0; total_size = 0
                msg_iter = client.iter_messages(source_id, reverse=True, reply_to=tid) if has_topics else client.iter_messages(source_id, reverse=True)
                async for msg in msg_iter:
                    if stop_event.is_set(): break
                    media = msg.media
                    text = msg.message or ""
                    if isinstance(media, MessageMediaWebPage): media = None
                    include = False; size = 0
                    if isinstance(media, MessageMediaPhoto):
                        include = filters.get("photos", False)
                        size = _photo_size(media)
                    elif isinstance(media, MessageMediaDocument):
                        if _is_gif_media(media):
                            include = False
                        elif _is_video_media(media):
                            include = filters.get("videos", False)
                        else:
                            include = filters.get("files", True)
                        size = _download_media_size(media)
                    elif text:
                        include = filters.get("text", False)
                        size = len(text.encode("utf-8")) + 1
                    if include:
                        count += 1
                        total_size += size or 0
                rows.append({"id": tid, "title": title, "count": count, "size": total_size})
                log_q.put(f"Download size: {title} — {count} item(s), {_fmt_bytes(total_size)}")
            result_q.put(rows)
        except Exception as e:
            log_q.put(f"ERROR calculating downloads: {e}"); result_q.put(None)
        finally:
            await client.disconnect()
    _new_loop(_r())
def run_download_topics(api_id, api_hash, source_id, source_name, output_dir, topics, selected_ids, filters, log_q, stop_event):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, MessageMediaWebPage
        except ImportError:
            log_q.put("ERROR: Telethon not installed."); log_q.put("__DONE__"); return
        try:
            if not output_dir:
                raise ValueError("No download folder was selected.")
            selected = set(int(x) for x in selected_ids)
            topic_rows = [t for t in topics if int(t["id"]) in selected]
            if not topic_rows:
                raise ValueError("No selected topics were found in this download project. Load the project and select the topics again.")
            root = os.path.join(output_dir, _safe_name(source_name, "source_group"))
            log_q.put(f"Preparing download folder: {root}")
            os.makedirs(root, exist_ok=True)
            state_file = os.path.join(root, f".tg_download_state_{abs(int(source_id))}.json")
        except Exception as e:
            log_q.put(f"FATAL ERROR preparing download: {e}")
            log_q.put("__DONE__")
            return
        state = {"topics": {}}
        if os.path.exists(state_file):
            try:
                with open(state_file, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update(loaded)
                log_q.put(f"Download state loaded: {len(state.get('topics', {}))} topic(s)")
            except Exception as e:
                log_q.put(f"WARNING: could not read download state: {e}")
        def save_download_state():
            _save_json_file(state_file, state, indent=2)
        def topic_state(tid):
            topics_state = state.setdefault("topics", {})
            ts = topics_state.setdefault(str(tid), {"last_msg_id": 0, "last_by_kind": {}, "downloaded": {}})
            ts.setdefault("last_by_kind", {})
            ts.setdefault("downloaded", {})
            return ts
        def selected_kinds():
            kinds = []
            if filters.get("files", True): kinds.append("file")
            if filters.get("photos", False): kinds.append("photo")
            if filters.get("videos", False): kinds.append("video")
            if filters.get("text", False): kinds.append("text")
            return kinds
        def min_selected_last(tstate):
            kinds = selected_kinds()
            if not kinds:
                return 0
            by_kind = tstate.setdefault("last_by_kind", {})
            return min(int(by_kind.get(k, 0) or 0) for k in kinds)
        def download_key(msg, kind, media):
            if kind == "text":
                return f"text:{msg.id}"
            fn = _get_fn(media)
            sz = _download_media_size(media)
            if fn and sz:
                return f"{kind}:{fn}:{sz}"
            try:
                mid = getattr(media.document, "id", None) or getattr(media.photo, "id", None)
            except Exception:
                mid = None
            return f"{kind}:{mid or msg.id}"
        def download_label(msg, kind, media):
            if kind == "text":
                return f"message_{msg.id}.txt"
            fn = _get_fn(media)
            if fn:
                return fn
            ext = ".jpg" if kind == "photo" else ".mp4" if kind == "video" else ""
            return f"{kind}_{msg.id}{ext}"
        def make_progress_callback(title, label):
            started = time.monotonic()
            last = {"time": 0.0, "pct": -1}
            def _cb(current, total):
                if not total:
                    return
                now = time.monotonic()
                pct = int((current / total) * 100)
                elapsed = max(0.001, now - started)
                speed = current / elapsed
                if now - last["time"] >= 1 or pct >= 100:
                    log_q.put(f"DOWNLOAD_STATUS [{title}] {label} — {_fmt_bytes(current)}/{_fmt_bytes(total)} ({pct}%) at {_fmt_bytes(speed)}/s")
                    last["time"] = now
                if pct >= 100 or pct >= last["pct"] + 10:
                    last["time"] = now
                    last["pct"] = pct
                    log_q.put(f"Downloading file: [{title}] {label} — {_fmt_bytes(current)}/{_fmt_bytes(total)} ({pct}%) at {_fmt_bytes(speed)}/s")
            return _cb
        client = TelegramClient("session_gui", api_id, api_hash)
        downloaded = skipped = errors = 0
        def include_download(msg):
            media = msg.media
            text = msg.message or ""
            if isinstance(media, MessageMediaWebPage): media = None
            if isinstance(media, MessageMediaPhoto):
                return filters.get("photos", False), "photo"
            if isinstance(media, MessageMediaDocument):
                if _is_gif_media(media):
                    return False, "gif"
                if _is_video_media(media):
                    return filters.get("videos", False), "video"
                return filters.get("files", True), "file"
            if text:
                return filters.get("text", False), "text"
            return False, "none"
        async def count_topic_items(tid, title, has_topics):
            count = 0
            tstate = topic_state(tid)
            last_id = min_selected_last(tstate)
            msg_iter = client.iter_messages(source_id, reverse=True, min_id=last_id, reply_to=tid) if has_topics else client.iter_messages(source_id, reverse=True, min_id=last_id)
            async for msg in msg_iter:
                if stop_event.is_set(): break
                include, kind = include_download(msg)
                kind_last = int(tstate.setdefault("last_by_kind", {}).get(kind, 0) or 0)
                if include and msg.id > kind_last:
                    count += 1
            log_q.put(f"Download pending: {title} — {count} item(s) after msg {last_id}")
            return count
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("ERROR: Not logged in."); log_q.put("__DONE__"); return
            src = await client.get_entity(source_id)
            has_topics = bool(getattr(src, "forum", False))
            log_q.put(f"Download destination: {root}")
            topic_totals = {}
            total_items = 0
            log_q.put("Counting download items...")
            for topic in topic_rows:
                if stop_event.is_set(): break
                tid = int(topic["id"])
                title = topic.get("title", "General")
                topic_total = await count_topic_items(tid, title, has_topics)
                topic_totals[tid] = topic_total
                total_items += topic_total
            log_q.put(f"Download items this run: {total_items}")
            processed_total = 0
            for topic in topic_rows:
                if stop_event.is_set(): break
                tid = int(topic["id"])
                title = topic.get("title", "General")
                topic_total = topic_totals.get(tid, 0)
                folder = os.path.join(root, _safe_name(title, f"topic_{tid}"))
                os.makedirs(folder, exist_ok=True)
                text_path = os.path.join(folder, "messages.txt")
                tstate = topic_state(tid)
                downloaded_keys = tstate.setdefault("downloaded", {})
                last_id = min_selected_last(tstate)
                log_q.put(f"Downloading topic: {title} — {topic_total} item(s) pending")
                topic_processed = topic_done = topic_skipped = topic_errors = 0
                msg_iter = client.iter_messages(source_id, reverse=True, min_id=last_id, reply_to=tid) if has_topics else client.iter_messages(source_id, reverse=True, min_id=last_id)
                async for msg in msg_iter:
                    if stop_event.is_set(): break
                    try:
                        media = msg.media
                        text = msg.message or ""
                        if isinstance(media, MessageMediaWebPage): media = None
                        include, kind = include_download(msg)
                        if not include:
                            continue
                        by_kind = tstate.setdefault("last_by_kind", {})
                        kind_last = int(by_kind.get(kind, 0) or 0)
                        if msg.id <= kind_last:
                            continue
                        key = download_key(msg, kind, media)
                        if key in downloaded_keys:
                            skipped += 1; topic_skipped += 1
                            topic_processed += 1; processed_total += 1
                            by_kind[kind] = max(kind_last, msg.id)
                            tstate["last_msg_id"] = max(int(tstate.get("last_msg_id", 0) or 0), msg.id)
                            tstate["last_by_kind"] = by_kind
                            save_download_state()
                            if topic_processed % 10 == 0 or topic_processed == topic_total or processed_total == total_items:
                                topic_pct = min(100.0, round((topic_processed / topic_total) * 100, 1)) if topic_total else 100.0
                                total_pct = min(100.0, round((processed_total / total_items) * 100, 1)) if total_items else 100.0
                                log_q.put(f"Download progress: [{title}] {topic_processed}/{topic_total} this topic ({topic_pct}%) | "
                                          f"{topic_skipped} skipped, {topic_errors} errors | "
                                          f"{processed_total}/{total_items} this download ({total_pct}%)")
                            continue
                        label = download_label(msg, kind, media)
                        if kind != "text":
                            size = _download_media_size(media)
                            log_q.put(f"Downloading file: [{title}] {label} — {_fmt_bytes(size)}")
                        if isinstance(media, MessageMediaPhoto):
                            if not filters.get("photos", False):
                                continue
                            saved = await client.download_media(msg, file=folder, progress_callback=make_progress_callback(title, label))
                        elif isinstance(media, MessageMediaDocument):
                            if _is_gif_media(media):
                                skipped += 1; topic_skipped += 1; continue
                            if _is_video_media(media):
                                if not filters.get("videos", False):
                                    continue
                            elif not filters.get("files", True):
                                continue
                            saved = await client.download_media(msg, file=folder, progress_callback=make_progress_callback(title, label))
                        elif text and filters.get("text", False):
                            log_q.put(f"Saving text: [{title}] message {msg.id}")
                            with open(text_path, "a", encoding="utf-8") as f:
                                f.write(f"[{msg.id}] {text}\n\n")
                            saved = text_path
                        else:
                            continue
                        if saved:
                            if kind != "text":
                                log_q.put(f"Downloaded file: [{title}] {os.path.basename(saved)}")
                            downloaded += 1; topic_done += 1; topic_processed += 1; processed_total += 1
                            downloaded_keys[key] = {"msg_id": msg.id, "path": saved}
                            by_kind[kind] = max(kind_last, msg.id)
                            tstate["last_msg_id"] = max(int(tstate.get("last_msg_id", 0) or 0), msg.id)
                            tstate["last_by_kind"] = by_kind
                            tstate["downloaded"] = downloaded_keys
                            save_download_state()
                            if topic_processed % 10 == 0 or topic_processed == topic_total or processed_total == total_items:
                                topic_pct = min(100.0, round((topic_processed / topic_total) * 100, 1)) if topic_total else 100.0
                                total_pct = min(100.0, round((processed_total / total_items) * 100, 1)) if total_items else 100.0
                                log_q.put(f"Download progress: [{title}] {topic_processed}/{topic_total} this topic ({topic_pct}%) | "
                                          f"{topic_skipped} skipped, {topic_errors} errors | "
                                          f"{processed_total}/{total_items} this download ({total_pct}%)")
                    except Exception as e:
                        errors += 1; topic_errors += 1
                        log_q.put(f"ERROR downloading [{title}] msg {getattr(msg, 'id', '-')}: {e}")
                log_q.put(f"Done topic: {title} — {topic_done} downloaded, {topic_skipped} skipped, {topic_errors} errors")
            log_q.put(f"Download complete: {downloaded} downloaded, {skipped} skipped, {errors} errors")
        except Exception as e:
            log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")
    _new_loop(_r())
