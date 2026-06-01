import asyncio
import difflib
import json
import os
import re
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, filedialog
import webbrowser
import time

CONFIG_FILE = "tg_backup_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"api_id":"","api_hash":"","phone":"","projects":[]}

def save_config(cfg):
    with open(CONFIG_FILE,"w") as f:
        json.dump(cfg, f, indent=2)

def _new_loop(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: loop.run_until_complete(coro)
    finally: loop.close()

_phone_code_hash = {}

# ── Telegram helpers ───────────────────────────────────────────────────────────

def do_send_code(api_id, api_hash, phone, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            log_q.put("ERROR: pip install telethon"); result_q.put(False); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            if await c.is_user_authorized():
                me = await c.get_me()
                log_q.put(f"Already logged in as {me.first_name} (@{me.username})")
                result_q.put(True); return
            sent = await c.send_code_request(phone)
            _phone_code_hash["hash"] = sent.phone_code_hash
            log_q.put("Code sent to your Telegram app.")
            result_q.put("need_code")
        except Exception as e:
            log_q.put(f"Login error: {e}"); result_q.put(False)
        finally:
            await c.disconnect()
    _new_loop(_r())

def do_signin(api_id, api_hash, phone, code, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            result_q.put(False); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            await c.sign_in(phone=phone, code=code, phone_code_hash=_phone_code_hash.get("hash",""))
            me = await c.get_me()
            log_q.put(f"Logged in as {me.first_name} (@{me.username})")
            result_q.put(True)
        except Exception as e:
            log_q.put(f"Sign in error: {e}"); result_q.put(False)
        finally:
            await c.disconnect()
    _new_loop(_r())

def list_groups(api_id, api_hash, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            result_q.put([]); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            if not await c.is_user_authorized():
                log_q.put("Not logged in."); result_q.put(None); return
            groups = []
            async for d in c.iter_dialogs():
                if d.is_group or d.is_channel:
                    groups.append({"name": d.name, "id": d.id})
            log_q.put(f"Found {len(groups)} groups/channels.")
            result_q.put(groups)
        except Exception as e:
            log_q.put(f"Error: {e}"); result_q.put([])
        finally:
            await c.disconnect()
    _new_loop(_r())

def list_topics(api_id, api_hash, group_id, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetForumTopicsRequest
        except ImportError:
            result_q.put([]); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            entity = await c.get_entity(group_id)
            if not getattr(entity, "forum", False):
                result_q.put([{"id":1,"title":"General (no topics)"}])
                return
            topics = []
            seen = set()
            offset_topic = 0; offset_id = 0; offset_date = None
            total = None
            while True:
                result = await c(GetForumTopicsRequest(peer=entity, offset_date=offset_date,
                    offset_id=offset_id, offset_topic=offset_topic, limit=100))
                if total is None and hasattr(result, 'count'):
                    total = result.count
                new_found = 0
                for t in result.topics:
                    if t.id not in seen:
                        seen.add(t.id); topics.append({"id":t.id,"title":t.title}); new_found += 1
                if new_found == 0 or not result.topics: break
                if total and len(seen) >= total: break
                last = result.topics[-1]
                offset_topic = last.id
                offset_id = getattr(last, 'top_message', 0) or 0
                offset_date = getattr(last, 'date', None)
            result_q.put(topics)
        except Exception as e:
            log_q.put(f"Could not load topics: {e}")
            result_q.put(None)
        finally:
            await c.disconnect()
    _new_loop(_r())

def create_group(api_id, api_hash, title, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.channels import CreateChannelRequest, ToggleForumRequest
        except ImportError:
            result_q.put(None); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            res = await c(CreateChannelRequest(title=title, about="", megagroup=True))
            ch = res.chats[0]
            log_q.put(f"Group created: {ch.title}")
            await c(ToggleForumRequest(channel=ch, enabled=True, tabs=False))
            log_q.put("Topics enabled!")
            result_q.put({"name":ch.title,"id":-int(f"100{ch.id}")})
        except Exception as e:
            log_q.put(f"Error: {e}"); result_q.put(None)
        finally:
            await c.disconnect()
    _new_loop(_r())

def create_topic_in_group(api_id, api_hash, group_id, title, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import CreateForumTopicRequest
        except ImportError:
            result_q.put(None); return
        c = TelegramClient("session_gui", api_id, api_hash)
        try:
            await c.connect()
            dst = await c.get_input_entity(group_id)
            new = await c(CreateForumTopicRequest(peer=dst, title=title))
            nid = new.updates[0].id
            log_q.put(f"Topic '{title}' created (id {nid})")
            result_q.put({"id":nid,"title":title})
        except Exception as e:
            log_q.put(f"Error: {e}"); result_q.put(None)
        finally:
            await c.disconnect()
    _new_loop(_r())

# ── Shared backup helpers ──────────────────────────────────────────────────────

async def _safe_req(fn, stop_event, log_q, retries=5):
    from telethon.errors import FloodWaitError
    attempt = 0
    while True:
        if stop_event.is_set(): raise InterruptedError("Stopped")
        try:
            return await fn()
        except FloodWaitError as e:
            log_q.put(f"Flood wait: {e.seconds}s (~{e.seconds//60} min)...")
            for _ in range(e.seconds + 5):
                if stop_event.is_set(): raise InterruptedError("Stopped")
                await asyncio.sleep(1)
            attempt = 0
        except InterruptedError:
            raise
        except Exception as e:
            attempt += 1
            wait = min(60, 15 * attempt)
            log_q.put(f"Request error ({e}), retry {attempt}/{retries} in {wait}s...")
            if attempt >= retries: raise
            await asyncio.sleep(wait)

async def _fetch_all_topics(client, src_entity, log_q):
    """Fetch ALL topics from a forum group, handling pagination correctly."""
    from telethon.tl.functions.messages import GetForumTopicsRequest
    topics = {}
    seen = set()
    offset_topic = 0; offset_id = 0; offset_date = None; page = 1; total = None
    while True:
        log_q.put(f"Fetching topic page {page}...")
        result = await client(GetForumTopicsRequest(
            peer=src_entity,
            offset_date=offset_date,
            offset_id=offset_id,
            offset_topic=offset_topic,
            limit=100))
        if total is None and hasattr(result, 'count'):
            total = result.count
            log_q.put(f"Source has {total} topics total")
        log_q.put(f"Got {len(result.topics)} topics (fetched so far: {len(seen)})")
        if not result.topics: break
        new_count = 0
        for t in result.topics:
            if t.id not in seen:
                seen.add(t.id); topics[t.id] = t.title; new_count += 1
        if new_count == 0: break
        if total and len(seen) >= total:
            log_q.put(f"All {total} topics fetched"); break
        # Use all three offset fields for correct Telegram pagination
        last = result.topics[-1]
        offset_topic = last.id
        offset_id = getattr(last, 'top_message', 0) or 0
        offset_date = getattr(last, 'date', None)
        page += 1
    return topics  # {topic_id: title}


def _get_fn(media):
    try:
        for a in media.document.attributes:
            if hasattr(a,'file_name') and a.file_name: return a.file_name
    except: pass
    return None

def _get_sz(media):
    try: return media.document.size
    except: return None

def _is_dup(sent_files, tid, fn, sz):
    if not fn or not sz: return False
    return sent_files.get(str(tid), {}).get(fn) == sz

def _track(sent_files, tid, fn, sz):
    if not fn or not sz: return
    k = str(tid)
    if k not in sent_files: sent_files[k] = {}
    sent_files[k][fn] = sz

def _safe_name(name, fallback="item"):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or fallback

def _fmt_bytes(n):
    n = int(n or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{n} B"
        size /= 1024

def _doc_mime(media):
    try: return (media.document.mime_type or "").lower()
    except Exception: return ""

def _is_gif_media(media):
    mime = _doc_mime(media)
    if mime == "image/gif": return True
    try:
        from telethon.tl.types import DocumentAttributeAnimated
        return any(isinstance(a, DocumentAttributeAnimated) for a in media.document.attributes)
    except Exception:
        return False

def _is_video_media(media):
    mime = _doc_mime(media)
    if not mime.startswith("video/") or _is_gif_media(media):
        return False
    try:
        from telethon.tl.types import DocumentAttributeVideo
        return any(isinstance(a, DocumentAttributeVideo) for a in media.document.attributes)
    except Exception:
        return mime.startswith("video/")

def _is_download_file(media):
    mime = _doc_mime(media)
    if not media or _is_gif_media(media) or _is_video_media(media):
        return False
    return bool(getattr(media, "document", None)) and not mime.startswith("image/")

def _photo_size(media):
    try:
        sizes = getattr(media.photo, "sizes", []) or []
        return max((getattr(s, "size", 0) or 0) for s in sizes) if sizes else 0
    except Exception:
        return 0

def _download_media_size(media):
    try:
        return media.document.size
    except Exception:
        return _photo_size(media)

def _get_src_thread(msg, known_topic_ids=None):
    known_topic_ids = known_topic_ids or set()
    if msg.id in known_topic_ids:
        return msg.id
    if msg.reply_to:
        top = getattr(msg.reply_to, "reply_to_top_id", None)
        if top:
            return top
        reply_to_msg_id = getattr(msg.reply_to, "reply_to_msg_id", None)
        if reply_to_msg_id in known_topic_ids:
            return reply_to_msg_id
        if getattr(msg.reply_to, "forum_topic", False):
            return reply_to_msg_id
    return 1

# ── Mode A: Auto backup ────────────────────────────────────────────────────────

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
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump({"thread_map":thread_map,"last_msg_id":last_msg_id[0],"topic_last_msg_id":topic_last_msg_id,"sent_files":sent_files}, f)

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
                    log_q.put(f"  → dest id {nid}")
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
                    from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                    media = msg.media
                    text = msg.message or ""
                    if isinstance(media, MessageMediaWebPage): media = None

                    # Apply filters
                    is_photo = isinstance(media, MessageMediaPhoto)
                    is_file = isinstance(media, MessageMediaDocument)
                    is_text = not media and bool(text)

                    skip_this = False
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

                    if media:
                        fn = _get_fn(media); sz = _get_sz(media)
                        if filters.get("skip_duplicates", True) and _is_dup(sent_files, dest_thread, fn, sz):
                            log_q.put(f"  Skipping duplicate: {fn}")
                            skipped += 1
                            topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                        else:
                            await safe(lambda: client.send_file(dest_id, media, caption=text, **kwargs))
                            _track(sent_files, dest_thread, fn, sz)
                            copied += 1
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
                    err_str = str(e)
                    if "premium account is required" in err_str.lower():
                        log_q.put(f"  ⚠ Skipping msg {msg.id}: requires Telegram Premium (large file or exclusive content)")
                        skipped += 1
                        topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                    elif "protected chat" in err_str.lower():
                        log_q.put(f"  ⚠ Skipping msg {msg.id}: protected chat content")
                        skipped += 1
                        topic_skipped[src_thread] = topic_skipped.get(src_thread, 0) + 1
                    else:
                        errors += 1
                        topic_errors[src_thread] = topic_errors.get(src_thread, 0) + 1
                        log_q.put(f"ERROR on msg {msg.id}: {e}")

            save_state()
            log_q.put(f"\n✓  Done. {copied} copied, {skipped} skipped, {errors} errors across {len(topic_pairs)} route(s).")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")

    _new_loop(_r())


# ── Mode B: Manual mapping ─────────────────────────────────────────────────────

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
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f)

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

                src_name = m.get('src_group_name','?')
                src_t    = m.get('src_topic_title','General')
                dst_t    = m.get('dest_topic_title','?')
                log_q.put(f"\n── {src_name} / {src_t}  →  {dst_t} ──")
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
                async for msg in msg_iter:
                    if stop_event.is_set(): log_q.put("Stopped."); break
                    try:
                        from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                        media = msg.media; text = msg.message or ""
                        if isinstance(media, MessageMediaWebPage): media = None

                        is_photo = isinstance(media, MessageMediaPhoto)
                        is_file  = isinstance(media, MessageMediaDocument)
                        is_text  = not media and bool(text)

                        skip_this = False
                        if is_text  and not filters.get("messages", True): skip_this = True
                        if is_photo and not filters.get("images",   True): skip_this = True
                        if is_file  and not filters.get("files",    True): skip_this = True

                        if skip_this:
                            ms["last_msg_id"] = msg.id; save_state(); skipped += 1
                            await asyncio.sleep(0.1); continue

                        kwargs = {"reply_to": dest_topic_id}
                        if media:
                            fn = _get_fn(media); sz = _get_sz(media)
                            if filters.get("skip_duplicates",True) and _is_dup(sent_files, dest_topic_id, fn, sz):
                                log_q.put(f"  Skipping duplicate: {fn}"); skipped += 1
                            else:
                                await safe(lambda: client.send_file(dest_group_id, media, caption=text, **kwargs))
                                _track(sent_files, dest_topic_id, fn, sz); copied += 1
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
                        errors += 1; log_q.put(f"   ERROR msg {msg.id}: {e}")

                log_q.put(f"   Done: {copied} copied, {skipped} skipped, {errors} errors")

            save_state()
            log_q.put("\nAll mappings complete.")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")

    _new_loop(_r())


# ── GUI colours ────────────────────────────────────────────────────────────────
def run_backup_routes(api_id, api_hash, routes, state_file, log_q, stop_event, filters):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import CreateForumTopicRequest, GetForumTopicsRequest
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

        def save_state():
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f)

        async def safe(fn): return await _safe_req(fn, stop_event, log_q)

        async def source_has_topics(client, group_id):
            try:
                entity = await client.get_entity(group_id)
                if not getattr(entity, "forum", False):
                    return False
                await client(GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=1))
                return True
            except Exception:
                return False

        topic_name_cache = {}

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

        def is_generic_topic_title(title, topic_id):
            title = (title or "").strip()
            return not title or title == f"Topic {topic_id}"

        async def enrich_route_names(client, route, src_forum, dest_topic_id):
            src_topic_id = route.get("src_topic_id", 1)
            if src_forum and is_generic_topic_title(route.get("src_topic_title"), src_topic_id):
                names = await topic_names(client, route["src_group_id"])
                route["src_topic_title"] = names.get(src_topic_id, route.get("src_topic_title") or f"Topic {src_topic_id}")
            if dest_topic_id and is_generic_topic_title(route.get("dest_topic_title"), dest_topic_id):
                names = await topic_names(client, route["dest_group_id"])
                route["dest_topic_title"] = names.get(dest_topic_id, route.get("dest_topic_title") or f"Topic {dest_topic_id}")

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

        async def ensure_dest_topic(client, route):
            dest_id = route.get("dest_topic_id")
            if dest_id:
                return dest_id
            title = route.get("dest_topic_title", "").strip()
            if not title:
                raise RuntimeError(f"Route has no destination topic: {route.get('src_group_name','?')} / {route.get('src_topic_title','?')}")
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
            log_q.put("Preparing routes...")
            for route in routes:
                if stop_event.is_set(): break
                dest_topic_id = await ensure_dest_topic(client, route)
                src_topic_id = route.get("src_topic_id", 1)
                src_forum = route.get("source_has_topics")
                if src_forum is None:
                    src_forum = await source_has_topics(client, route["src_group_id"])
                await enrich_route_names(client, route, src_forum, dest_topic_id)
                key = f"{route['src_group_id']}_{src_topic_id}_{route['dest_group_id']}_{dest_topic_id}"
                ms = state.setdefault(key, {"last_msg_id":0,"sent_files":{}})
                last_id = ms.get("last_msg_id", 0)
                route_total = await count_iter_messages(client, route["src_group_id"], last_id, src_forum, src_topic_id)
                total_remaining += route_total
                prepared.append((route, dest_topic_id, src_forum, key, ms, route_total))

            log_q.put(f"Total messages to copy across all routes: {total_remaining}\n")
            copied = skipped = errors = processed_total = 0

            for route, dest_topic_id, src_forum, key, ms, route_total in prepared:
                if stop_event.is_set():
                    log_q.put("Stopped between routes.")
                    break
                src_topic_id = route.get("src_topic_id", 1)
                last_id = ms.get("last_msg_id", 0)
                sent_files = ms.setdefault("sent_files", {})
                route_label = f"{route.get('src_group_name','?')} / {route.get('src_topic_title','General')} -> {route.get('dest_group_name','?')} / {route.get('dest_topic_title','?')}"
                progress_label = route_label.replace(" -> ", " → ")
                log_q.put(f"\n▶  Route: {route_label}")
                log_q.put(f"   Messages to copy this topic: {route_total} | resuming from msg {last_id}")
                if src_forum:
                    msg_iter = client.iter_messages(route["src_group_id"], reverse=True, min_id=last_id, reply_to=src_topic_id)
                else:
                    msg_iter = client.iter_messages(route["src_group_id"], reverse=True, min_id=last_id)

                processed_route = 0
                route_copied = route_skipped = route_errors = 0
                async for msg in msg_iter:
                    if stop_event.is_set():
                        log_q.put(f"Stopped at message {ms.get('last_msg_id', 0)}."); break
                    try:
                        from telethon.tl.types import MessageMediaWebPage, MessageMediaDocument, MessageMediaPhoto
                        media = msg.media; text = msg.message or ""
                        if isinstance(media, MessageMediaWebPage): media = None

                        is_photo = isinstance(media, MessageMediaPhoto)
                        is_file  = isinstance(media, MessageMediaDocument)
                        is_text  = not media and bool(text)

                        skip_this = False
                        if is_text  and not filters.get("messages", True): skip_this = True
                        if is_photo and not filters.get("images",   True): skip_this = True
                        if is_file  and not filters.get("files",    True): skip_this = True

                        if skip_this:
                            skipped += 1; route_skipped += 1
                        else:
                            kwargs = {"reply_to": dest_topic_id}
                            if media:
                                fn = _get_fn(media); sz = _get_sz(media)
                                if filters.get("skip_duplicates", True) and _is_dup(sent_files, dest_topic_id, fn, sz):
                                    skipped += 1; route_skipped += 1
                                else:
                                    await safe(lambda: client.send_file(route["dest_group_id"], media, caption=text, **kwargs))
                                    _track(sent_files, dest_topic_id, fn, sz)
                                    copied += 1; route_copied += 1
                            elif text:
                                await safe(lambda: client.send_message(route["dest_group_id"], text, **kwargs))
                                copied += 1; route_copied += 1

                        # Always save progress — even for skipped messages
                        ms["last_msg_id"] = msg.id; ms["sent_files"] = sent_files
                        save_state()
                        processed_route += 1; processed_total += 1
                        report_every = max(1, min(10, route_total // 20)) if route_total else 10
                        if processed_route % report_every == 0 or processed_route == route_total:
                            rpct = min(100.0, round((processed_route/route_total)*100,1)) if route_total else 100.0
                            tpct = min(100.0, round((processed_total/total_remaining)*100,1)) if total_remaining else 100.0
                            log_q.put(f"Progress: [{progress_label}] "
                                      f"{processed_route}/{route_total} this topic ({rpct}%) | "
                                      f"{route_skipped} skipped, {route_errors} errors | "
                                      f"{processed_total}/{total_remaining} this run ({tpct}%)")
                        await asyncio.sleep(1)
                    except InterruptedError: break
                    except Exception as e:
                        errors += 1; route_errors += 1
                        log_q.put(f"ERROR [{route_label}] msg {msg.id}: {e}")

            save_state()
            log_q.put(f"\n✓  Done. {copied} copied, {skipped} skipped, {errors} errors across {len(prepared)} route(s).")
        except InterruptedError: log_q.put("Stopped by user.")
        except Exception as e: log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")

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

        selected = set(int(x) for x in selected_ids)
        topic_rows = [t for t in topics if int(t["id"]) in selected]
        root = os.path.join(output_dir, _safe_name(source_name, "source_group"))
        os.makedirs(root, exist_ok=True)
        state_file = os.path.join(root, f".tg_download_state_{abs(int(source_id))}.json")
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
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)

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
                        log_q.put(f"ERROR downloading [{title}] msg {getattr(msg, 'id', '?')}: {e}")
                log_q.put(f"Done topic: {title} — {topic_done} downloaded, {topic_skipped} skipped, {topic_errors} errors")
            log_q.put(f"Download complete: {downloaded} downloaded, {skipped} skipped, {errors} errors")
        except Exception as e:
            log_q.put(f"FATAL ERROR: {e}")
        finally:
            await client.disconnect(); log_q.put("__DONE__")
    _new_loop(_r())


BG="#0f1923";BG2="#1a2635";BG3="#243447";ACCENT="#4fc3f7";SUCCESS="#69f0ae"
WARNING="#ffd740";DANGER="#ff5252";TEXT="#e8eaf0";MUTED="#78909c"
FONT=("Segoe UI",10);FONT_SM=("Segoe UI",9);FONT_B=("Segoe UI",10,"bold")
FONT_H=("Segoe UI",11,"bold");MONO=("Consolas",9)
APP_NAME = "Telegram Group Backup & Download"
APP_VERSION = "v1.0"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"

def make_scrollable(parent):
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0)
    sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y); canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    inner = tk.Frame(canvas, bg=BG)
    wid = canvas.create_window((0,0), window=inner, anchor="nw")
    def _resize(e): canvas.itemconfig(wid, width=e.width)
    def _change(e): canvas.configure(scrollregion=canvas.bbox("all"))
    def _wheel(e): canvas.yview_scroll(int(-1*(e.delta/120)),"units")
    def _bind_all(w):
        if isinstance(w, ttk.Treeview):
            return
        w.bind("<MouseWheel>", _wheel, add="+")
        for c in w.winfo_children(): _bind_all(c)
    canvas.bind("<Configure>", _resize); inner.bind("<Configure>", _change)
    canvas.bind("<MouseWheel>", _wheel); inner.bind("<MouseWheel>", _wheel)
    inner.bind("<Map>", lambda e: _bind_all(inner), add="+")
    return canvas, inner

def card(parent, title=None, pady=16):
    f = tk.Frame(parent, bg=BG2, padx=20, pady=pady)
    if title:
        tk.Label(f, text=title, bg=BG2, fg=ACCENT, font=FONT_H).pack(anchor="w")
        tk.Frame(f, bg=ACCENT, height=1).pack(fill=tk.X, pady=(4,10))
    return f

def field_row(parent, label, var, width=24, hint=None):
    row = tk.Frame(parent, bg=BG2); row.pack(fill=tk.X, pady=3)
    tk.Label(row, text=label, bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
    ttk.Entry(row, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0,8))
    if hint: tk.Label(row, text=hint, bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(side=tk.LEFT)

def combo_row(parent, label, var, width=42):
    row = tk.Frame(parent, bg=BG2); row.pack(fill=tk.X, pady=3)
    tk.Label(row, text=label, bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
    cb = ttk.Combobox(row, textvariable=var, width=width, state="readonly"); cb.pack(side=tk.LEFT)
    return cb

def check_row(parent, label, var):
    row = tk.Frame(parent, bg=BG2); row.pack(anchor="w", pady=2)
    tk.Checkbutton(row, text=label, variable=var, bg=BG2, fg=TEXT, activebackground=BG2,
                   activeforeground=TEXT, selectcolor=BG3, font=FONT).pack(side=tk.LEFT)

# ── App ────────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE); self.geometry("980x760")
        self.minsize(820,640); self.configure(bg=BG)
        self.config_data = load_config()
        self.groups = []; self.log_queue = queue.Queue(); self.result_queue = queue.Queue()
        self.stop_event = threading.Event(); self.backup_running = False
        self.active_task = None
        self.current_project_name = None
        self.activity_queue = list(self.config_data.get("activity_queue", []))
        self.queue_running = False; self.skip_current_for_queue = False
        self._m_src_topics = []; self._m_dst_topics = []
        self._manual_mappings = []
        self._b_src_topics = []; self._b_dst_topics = []; self._b_routes = []
        self._d_topics = []; self._d_topic_view = []
        self._b_loaded_project_index = None
        self._styles(); self._build()
        self.after(300, self._poll)

    def _styles(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=MUTED, padding=[18,10], font=FONT, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected",BG3)], foreground=[("selected",ACCENT)])
        s.configure("TFrame", background=BG); s.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        s.configure("TEntry", fieldbackground=BG3, foreground=TEXT, insertcolor=ACCENT, font=FONT, padding=6)
        s.configure("TCombobox", fieldbackground=BG3, foreground=TEXT, selectbackground=BG3, selectforeground=TEXT, font=FONT, padding=5)
        s.map("TCombobox", fieldbackground=[("readonly",BG3)], foreground=[("readonly",TEXT)])
        s.configure("P.TButton", background=ACCENT, foreground=BG, font=FONT_B, padding=[14,8], borderwidth=0)
        s.configure("TButton", background=BG3, foreground=TEXT, font=FONT, padding=[12,7], borderwidth=0)
        s.configure("D.TButton", background=DANGER, foreground="white", font=FONT_B, padding=[12,7], borderwidth=0)
        s.configure("G.TButton", background=BG2, foreground=MUTED, font=FONT_SM, padding=[10,5], borderwidth=0)
        s.map("P.TButton", background=[("active","#29b6f6"),("disabled",BG3)])
        s.map("TButton",   background=[("active",BG2),("disabled",BG2)], foreground=[("disabled",MUTED)])
        s.map("D.TButton", background=[("active","#ff1744"),("disabled",BG3)])
        s.map("G.TButton", background=[("active",BG3),("disabled",BG2)], foreground=[("disabled",MUTED)])

    def _build(self):
        hdr = tk.Frame(self, bg=BG2); hdr.pack(fill=tk.X)
        ih = tk.Frame(hdr, bg=BG2, padx=22, pady=14); ih.pack(fill=tk.X)
        tk.Label(ih, text=APP_TITLE, bg=BG2, fg=TEXT,
                 font=("Segoe UI",16,"bold")).pack(side=tk.LEFT)
        tk.Label(ih, text="By Poleroso · Projects, downloads, and queued runs", bg=BG2, fg=MUTED,
                 font=FONT_SM).pack(side=tk.LEFT, padx=(16,0))
        tk.Frame(self, bg=ACCENT, height=2).pack(fill=tk.X)
        self.nb = ttk.Notebook(self); self.nb.pack(fill=tk.BOTH, expand=True)
        t1=ttk.Frame(self.nb); t2=ttk.Frame(self.nb); t3=ttk.Frame(self.nb); t4=ttk.Frame(self.nb)
        self.nb.add(t1, text="  ①  Setup  ")
        self.nb.add(t2, text="  ②  Project Builder  ")
        self.nb.add(t3, text="  ③  Download  ")
        self.nb.add(t4, text="  ④  Run  ")
        self._setup(t1); self._project_builder(t2); self._download_tab(t3); self._run_tab(t4)

    # ── SETUP ──────────────────────────────────────────────────────────────────
    def _setup(self, parent):
        _, inner = make_scrollable(parent)
        c1 = card(inner, "How to get your API credentials"); c1.pack(fill=tk.X, padx=20, pady=(20,8))
        for n,t in [("1","Go to  https://my.telegram.org"),("2","Log in with your phone number"),
                    ("3","Click 'API development tools'"),("4","Create an app — any name"),
                    ("5","Copy your api_id and api_hash below")]:
            r=tk.Frame(c1,bg=BG2); r.pack(fill=tk.X,pady=2)
            tk.Label(r,text=f" {n} ",bg=ACCENT,fg=BG,font=("Segoe UI",8,"bold"),width=2).pack(side=tk.LEFT,padx=(0,8))
            tk.Label(r,text=t,bg=BG2,fg=TEXT,font=FONT).pack(side=tk.LEFT)
        lnk=tk.Label(c1,text="→  Open my.telegram.org",bg=BG2,fg="#29b6f6",font=("Segoe UI",10,"underline"),cursor="hand2")
        lnk.pack(anchor="w",pady=(10,0)); lnk.bind("<Button-1>",lambda e:webbrowser.open("https://my.telegram.org"))

        c2=card(inner,"Your credentials"); c2.pack(fill=tk.X,padx=20,pady=8)
        self.api_id_var=tk.StringVar(value=self.config_data.get("api_id",""))
        self.api_hash_var=tk.StringVar(value=self.config_data.get("api_hash",""))
        self.phone_var=tk.StringVar(value=self.config_data.get("phone",""))
        field_row(c2,"API ID",self.api_id_var,18); field_row(c2,"API Hash",self.api_hash_var,36)
        field_row(c2,"Phone",self.phone_var,22,hint="e.g. +34612345678")
        br=tk.Frame(c2,bg=BG2); br.pack(fill=tk.X,pady=(12,0))
        ttk.Button(br,text="Save & Connect",style="P.TButton",command=self._connect).pack(side=tk.LEFT)
        self.conn_lbl=tk.Label(br,text="",bg=BG2,fg=SUCCESS,font=FONT_B); self.conn_lbl.pack(side=tk.LEFT,padx=14)

        self.code_card=card(inner,"Enter your Telegram code")
        tk.Label(self.code_card,text="Enter the code Telegram sent to your app:",bg=BG2,fg=TEXT,font=FONT).pack(anchor="w",pady=(0,8))
        cr=tk.Frame(self.code_card,bg=BG2); cr.pack(anchor="w")
        self.code_var=tk.StringVar()
        self.code_entry=ttk.Entry(cr,textvariable=self.code_var,width=12,font=("Segoe UI",16))
        self.code_entry.pack(side=tk.LEFT,padx=(0,12))
        ttk.Button(cr,text="Confirm →",style="P.TButton",command=self._confirm_code).pack(side=tk.LEFT)

        c3=card(inner,"Activity log"); c3.pack(fill=tk.X,padx=20,pady=(8,20))
        self.setup_log=scrolledtext.ScrolledText(c3,height=7,bg=BG,fg=TEXT,font=MONO,insertbackground=ACCENT,bd=0,relief="flat",padx=8,pady=8)
        self.setup_log.pack(fill=tk.X)
        self.setup_log.tag_config("ok",foreground=SUCCESS); self.setup_log.tag_config("err",foreground=DANGER)

    def _connect(self):
        if self._telegram_busy("connect or log in"):
            return
        ai=self.api_id_var.get().strip(); ah=self.api_hash_var.get().strip(); ph=self.phone_var.get().strip()
        if not ai or not ah or not ph: messagebox.showerror("Missing","Fill in API ID, Hash and Phone."); return
        self.config_data.update({"api_id":ai,"api_hash":ah,"phone":ph}); save_config(self.config_data)
        self.conn_lbl.config(text="Connecting...",fg=WARNING); self._log_s("Connecting...")
        threading.Thread(target=lambda:do_send_code(int(ai),ah,ph,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_login)

    def _chk_login(self):
        try:
            r=self.result_queue.get_nowait()
            if r is True: self.conn_lbl.config(text="✓  Connected",fg=SUCCESS); self._load_groups()
            elif r=="need_code": self.conn_lbl.config(text="Check Telegram for code",fg=WARNING); self._show_code()
            else: self.conn_lbl.config(text="✗  Failed",fg=DANGER)
        except queue.Empty: self.after(500,self._chk_login)

    def _show_code(self):
        self.code_card.pack(fill=tk.X,padx=20,pady=8); self.code_entry.focus()

    def _confirm_code(self):
        if self._telegram_busy("confirm the login code"):
            return
        threading.Thread(target=lambda:do_signin(int(self.api_id_var.get()),self.api_hash_var.get(),
            self.phone_var.get(),self.code_var.get(),self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_signin)

    def _chk_signin(self):
        try:
            r=self.result_queue.get_nowait()
            if r is True: self.conn_lbl.config(text="✓  Logged in",fg=SUCCESS); self.code_card.pack_forget(); self._load_groups()
            else: self.conn_lbl.config(text="✗  Wrong code",fg=DANGER)
        except queue.Empty: self.after(500,self._chk_signin)

    def _telegram_busy(self, action="use Telegram"):
        if not self.backup_running:
            return False
        msg = f"A task is running. Wait until it finishes before you {action}."
        messagebox.showinfo("Task running", msg)
        self._log_s(msg)
        if hasattr(self, "run_log"):
            self._log_r(msg, "y")
        return True

    def _load_groups(self):
        if self._telegram_busy("refresh groups"):
            return
        self._log_s("Loading groups...")
        threading.Thread(target=lambda:list_groups(int(self.api_id_var.get()),self.api_hash_var.get(),
            self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_groups)

    def _chk_groups(self):
        try:
            r=self.result_queue.get_nowait()
            if isinstance(r,list) and r:
                self.groups=r; self._refresh_combos()
            elif r is None: self._log_s("Not logged in yet.")
            else: self.after(500,self._chk_groups)
        except queue.Empty: self.after(500,self._chk_groups)

    def _log_s(self,msg,tag=None):
        self.setup_log.insert(tk.END,msg+"\n",tag or ""); self.setup_log.see(tk.END)

    # ── AUTO BACKUP ────────────────────────────────────────────────────────────
    def _group_from_selection(self, text):
        return next((g for g in self.groups if f"(id: {g['id']})" in text), None) or next((g for g in self.groups if g["name"] in text), None)

    # ── PROJECT BUILDER ─────────────────────────────────────────────────────

    # ── PROJECT BUILDER ────────────────────────────────────────────────────────

    def _project_builder(self, parent):
        """Destination-first project builder with a simple grouped route list."""
        self._b_routes = []
        self._b_dest_topics = []
        self._b_src_topics = []
        self._b_dest_group_id = None
        self._b_dest_group_name = ""

        # Main scrollable area
        _, inner = make_scrollable(parent)

        # ── STEP 1: Project name + open existing ──────────────────────────
        intro = card(inner, "Project Builder"); intro.pack(fill=tk.X, padx=20, pady=(16,6))
        tk.Label(intro, text="Project -> Destination group -> Add sources -> Check routes -> Save.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w")

        s1 = card(inner, "Project"); s1.pack(fill=tk.X, padx=20, pady=6)

        r0 = tk.Frame(s1, bg=BG2); r0.pack(fill=tk.X, pady=3)
        tk.Label(r0, text="Project name:", bg=BG2, fg=TEXT, font=FONT, width=16, anchor="w").pack(side=tk.LEFT)
        self.b_name_var = tk.StringVar()
        ttk.Entry(r0, textvariable=self.b_name_var, width=30).pack(side=tk.LEFT, padx=(0,16))

        r0b = tk.Frame(s1, bg=BG2); r0b.pack(fill=tk.X, pady=3)
        tk.Label(r0b, text="Edit existing:", bg=BG2, fg=MUTED, font=FONT_SM, width=16, anchor="w").pack(side=tk.LEFT)
        self.b_existing_var = tk.StringVar()
        self.b_existing_combo = ttk.Combobox(r0b, textvariable=self.b_existing_var, width=28, state="readonly")
        self.b_existing_combo.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r0b, text="Load Project", command=self._builder_load_project).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r0b, text="Delete Project", style="G.TButton",
                   command=self._builder_delete_project).pack(side=tk.LEFT)
        self._builder_refresh_project_combo()

        # ── STEP 2: Destination group ──────────────────────────────────────
        s2 = card(inner, "Destination group"); s2.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s2, text="Choose where messages will be copied to.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))

        dest_choices = tk.Frame(s2, bg=BG2); dest_choices.pack(fill=tk.X, pady=3)
        tk.Label(dest_choices, text="Use existing group:", bg=BG2, fg=TEXT, font=FONT_B, anchor="w").pack(side=tk.LEFT, padx=(0,8))
        self.b_dest_var = tk.StringVar()
        self.b_dest_combo = ttk.Combobox(dest_choices, textvariable=self.b_dest_var, width=26, state="readonly")
        self.b_dest_combo.pack(side=tk.LEFT, padx=(0,8))
        self.b_dest_combo.bind("<<ComboboxSelected>>", lambda e: self._builder_load_dest_topics())
        ttk.Button(dest_choices, text="Use This Group", style="P.TButton",
                   command=self._builder_load_dest_topics).pack(side=tk.LEFT, padx=(0,14))

        tk.Label(dest_choices, text="or create new:", bg=BG2, fg=TEXT, font=FONT_B, anchor="w").pack(side=tk.LEFT, padx=(0,8))
        self.new_group_name = tk.StringVar()
        ttk.Entry(dest_choices, textvariable=self.new_group_name, width=22).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(dest_choices, text="Create This Group",
                   command=self._create_group).pack(side=tk.LEFT)

        self.b_dest_status = tk.Label(s2, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_dest_status.pack(anchor="w", pady=(4,0))

        # Optional: create a new topic in destination
        r2b = tk.Frame(s2, bg=BG2); r2b.pack(fill=tk.X, pady=(14,0))
        tk.Label(r2b, text="Create topic in destination group:", bg=BG2, fg=MUTED, font=FONT_SM, width=30, anchor="w").pack(side=tk.LEFT)
        self.b_new_topic_var = tk.StringVar()
        ttk.Entry(r2b, textvariable=self.b_new_topic_var, width=24).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r2b, text="Create Topic", command=self._builder_add_bucket).pack(side=tk.LEFT)

        # ── STEP 3: Source topics ──────────────────────────────────────────
        s3 = card(inner, "Source topics to copy"); s3.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s3, text="Choose a source group, load its topics, then add one topic or all topics to the route list.",
                 bg=BG2, fg=MUTED, font=FONT_SM, justify="left").pack(anchor="w", pady=(0,8))

        r3 = tk.Frame(s3, bg=BG2); r3.pack(fill=tk.X, pady=3)
        tk.Label(r3, text="Source group:", bg=BG2, fg=TEXT, font=FONT, width=18, anchor="w").pack(side=tk.LEFT)
        self.b_src_var = tk.StringVar()
        self.b_src_combo = ttk.Combobox(r3, textvariable=self.b_src_var, width=34, state="readonly")
        self.b_src_combo.pack(side=tk.LEFT, padx=(0,10))
        self.b_src_combo.bind("<<ComboboxSelected>>", lambda e: self._builder_load_source_topics())
        ttk.Button(r3, text="Reload Source Topics", command=self._builder_load_source_topics).pack(side=tk.LEFT)

        self.b_src_status = tk.Label(s3, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_src_status.pack(anchor="w", pady=(2,4))

        r4 = tk.Frame(s3, bg=BG2); r4.pack(fill=tk.X, pady=3)
        tk.Label(r4, text="Source topic:", bg=BG2, fg=TEXT, font=FONT, width=18, anchor="w").pack(side=tk.LEFT)
        self.b_src_topic_var = tk.StringVar()
        self.b_src_topic_combo = ttk.Combobox(r4, textvariable=self.b_src_topic_var, width=34, state="readonly")
        self.b_src_topic_combo.pack(side=tk.LEFT)

        r4b = tk.Frame(s3, bg=BG2); r4b.pack(fill=tk.X, pady=4)
        self.b_whole_group_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r4b,
                       text="Copy whole group as one channel → all messages go into ONE destination topic",
                       variable=self.b_whole_group_var,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT)
        tk.Label(s3,
                 text="  ↑ Use this only for groups WITHOUT topics. For groups WITH topics, use 'Add ALL topics' below.",
                 bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(anchor="w", padx=(0,0), pady=(0,4))

        btn_row = tk.Frame(s3, bg=BG2); btn_row.pack(fill=tk.X, pady=(8,0))
        ttk.Button(btn_row, text="Add Selected Topic to Routes", command=lambda: self._builder_add_routes(False)).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(btn_row, text="Add All Topics to Routes", style="P.TButton",
                   command=lambda: self._builder_add_routes(True)).pack(side=tk.LEFT)

        # ── STEP 3b: Copy filters ─────────────────────────────────────────
        s3b = card(inner, "What to copy"); s3b.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s3b, text="These filters apply to ALL routes in this project.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))

        frow = tk.Frame(s3b, bg=BG2); frow.pack(fill=tk.X, pady=2)
        tk.Label(frow, text="Copy:", bg=BG2, fg=TEXT, font=FONT_SM).pack(side=tk.LEFT, padx=(0,10))
        self.b_f_msg = tk.BooleanVar(value=True)
        self.b_f_img = tk.BooleanVar(value=True)
        self.b_f_fil = tk.BooleanVar(value=True)
        for var, lbl in [(self.b_f_msg, "Text messages"), (self.b_f_img, "Images"), (self.b_f_fil, "Files")]:
            tk.Checkbutton(frow, text=lbl, variable=var,
                           bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                           selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,14))

        frow2 = tk.Frame(s3b, bg=BG2); frow2.pack(fill=tk.X, pady=2)
        self.b_f_skip_dup = tk.BooleanVar(value=True)
        tk.Checkbutton(frow2, text="Skip duplicate files (same name + size)",
                       variable=self.b_f_skip_dup,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(anchor="w")

        # ── STEP 4: Review routes ──────────────────────────────────────────
        s4 = card(inner, "Routes by destination"); s4.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s4,
                 text="Each source topic is copied into one destination topic. Suggestions are shown, but you choose what to use.",
                 bg=BG2, fg=MUTED, font=FONT_SM, justify="left").pack(anchor="w", pady=(0,10))

        abar = tk.Frame(s4, bg=BG2); abar.pack(fill=tk.X, pady=(0,8))
        ttk.Button(abar, text="Accept Suggestions", style="P.TButton",
                   command=self._builder_accept_suggestions).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Use Existing",
                   command=self._builder_use_existing_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Create",
                   command=self._builder_create_for_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Delete Selected",
                   style="G.TButton", command=self._builder_remove_selected).pack(side=tk.LEFT)

        self.b_status_lbl = tk.Label(s4, text="No routes yet — add sources above.",
                                     bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_status_lbl.pack(anchor="w", pady=(0,8))

        tree_frame = tk.Frame(s4, bg=BG2); tree_frame.pack(fill=tk.X)
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        self.b_routes_tree = ttk.Treeview(tree_frame, columns=("source", "dest", "status"),
                                          show="tree headings", height=14, selectmode="extended",
                                          yscrollcommand=tree_scroll.set)
        tree_scroll.config(command=self.b_routes_tree.yview)
        self.b_routes_tree.heading("#0", text="Destination")
        self.b_routes_tree.heading("source", text="Source")
        self.b_routes_tree.heading("dest", text="Topic")
        self.b_routes_tree.heading("status", text="Status")
        self.b_routes_tree.column("#0", width=210, anchor="w")
        self.b_routes_tree.column("source", width=250, anchor="w")
        self.b_routes_tree.column("dest", width=180, anchor="w")
        self.b_routes_tree.column("status", width=130, anchor="w")
        self.b_routes_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.b_routes_tree.bind("<Double-1>", lambda e: self._builder_use_existing_selected())
        self.b_routes_tree.bind("<Delete>", lambda e: self._builder_remove_selected())
        self.b_routes_tree.bind("<MouseWheel>", self._builder_tree_mousewheel)

        # ── STEP 5: Save ───────────────────────────────────────────────────
        s5 = card(inner); s5.pack(fill=tk.X, padx=20, pady=(6,20))
        foot = tk.Frame(s5, bg=BG2); foot.pack(fill=tk.X)
        ttk.Button(foot, text="Save Project", style="P.TButton",
                   command=self._builder_save_project).pack(side=tk.LEFT, padx=(0,12))
        ttk.Button(foot, text="Clear Routes", style="G.TButton",
                   command=self._builder_clear).pack(side=tk.LEFT)

    # ── Builder helpers ────────────────────────────────────────────────────────

    def _builder_refresh_project_combo(self):
        projects = list(self.config_data.get("projects", []))
        projects.sort(key=lambda p: ((p.get("dest_name") or "").lower(), p.get("name", "").lower()))
        self._b_project_display_to_name = {}
        names = []
        for p in projects:
            dest = p.get("dest_name") or "Archive"
            mode = "Builder" if p.get("mode") == "routes" else "Download" if p.get("mode") == "download" else "Classic"
            display = f"{dest} / {p['name']}  ({mode})"
            self._b_project_display_to_name[display] = p["name"]
            names.append(display)
        if hasattr(self, 'b_existing_combo'):
            self.b_existing_combo["values"] = names

    def _topic_display(self, topic):
        return f"{topic['title']}  (id:{topic['id']})"

    def _builder_parse_topic(self, text, topics):
        return next((t for t in topics if t["title"] in text), None)

    def _builder_load_dest_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel = self.b_dest_var.get()
        if not sel:
            messagebox.showerror("", "Select a destination group first."); return
        grp = next((g for g in self.groups if g["name"] in sel), None)
        if not grp: return
        self._b_dest_group_id = grp["id"]
        self._b_dest_group_name = grp["name"]
        if hasattr(self, 'b_dest_status'):
            self.b_dest_status.config(text="Loading topics...", fg=WARNING)
        self._log_s(f"Loading destination topics from '{grp['name']}'...")
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            grp["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, lambda: self._builder_chk_topics("dest"))

    def _builder_load_source_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel = self.b_src_var.get()
        if not sel:
            messagebox.showerror("", "Select a source group first."); return
        grp = next((g for g in self.groups if g["name"] in sel), None)
        if not grp: return
        if hasattr(self, 'b_src_status'):
            self.b_src_status.config(text="Loading topics...", fg=WARNING)
        self._log_s(f"Loading source topics from '{grp['name']}'...")
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            grp["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, lambda: self._builder_chk_topics("src"))

    def _builder_chk_topics(self, which):
        try:
            r = self.result_queue.get_nowait()
            if isinstance(r, list):
                if which == "dest":
                    self._b_dest_topics = r
                    if hasattr(self, 'b_dest_status'):
                        self.b_dest_status.config(
                            text=f"✓  {len(r)} destination topics loaded.", fg=SUCCESS)
                    self._builder_redraw()
                else:
                    self._b_src_topics = r
                    self.b_src_topic_combo["values"] = [self._topic_display(t) for t in r]
                    if r[0]["id"] == 1 and len(r) == 1:
                        # No topics — auto-check whole group
                        self.b_whole_group_var.set(True)
                        if hasattr(self, 'b_src_status'):
                            self.b_src_status.config(text="No topics — use 'Copy whole group' below.", fg=WARNING)
                    else:
                        self.b_whole_group_var.set(False)
                        if hasattr(self, 'b_src_status'):
                            self.b_src_status.config(
                                text=f"✓  {len(r)} topics loaded.", fg=SUCCESS)
        except queue.Empty:
            self.after(500, lambda: self._builder_chk_topics(which))

    def _builder_add_bucket(self):
        if self._telegram_busy("create a topic"):
            return
        title = self.b_new_topic_var.get().strip()
        if not title: messagebox.showerror("", "Enter a topic name."); return
        if not self._b_dest_group_id:
            messagebox.showerror("", "Load destination topics first."); return
        ai = self.api_id_var.get(); ah = self.api_hash_var.get()
        threading.Thread(target=lambda: create_topic_in_group(
            int(ai), ah, self._b_dest_group_id, title,
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._builder_chk_new_bucket)

    def _builder_chk_new_bucket(self):
        try:
            r = self.result_queue.get_nowait()
            if r:
                self._b_dest_topics.append(r)
                self.b_new_topic_var.set("")
                self._log_s(f"Topic '{r['title']}' created.")
                if hasattr(self, 'b_dest_status'):
                    self.b_dest_status.config(
                        text=f"✓  {len(self._b_dest_topics)} topics  ('{r['title']}' added)", fg=SUCCESS)
                self._builder_redraw()
            else:
                messagebox.showerror("Error", "Could not create topic.")
        except queue.Empty:
            self.after(500, self._builder_chk_new_bucket)

    def _builder_match_dest(self, src_title):
        src_lower = src_title.lower().strip()
        for t in self._b_dest_topics:
            if t["title"].lower().strip() == src_lower:
                return t, "exact"
        for t in self._b_dest_topics:
            d = t["title"].lower().strip()
            if len(d) >= 4 and len(src_lower) >= 4:
                if d in src_lower or src_lower in d:
                    return t, "suggested"
        src_words = set(w for w in src_lower.split() if len(w) >= 4)
        for t in self._b_dest_topics:
            d_words = set(w for w in t["title"].lower().split() if len(w) >= 4)
            if src_words & d_words:
                return t, "suggested"
        return None, None

    def _builder_add_routes(self, add_all=False):
        if not self._b_dest_topics:
            messagebox.showerror("", "Load destination topics first."); return
        sel_src = self.b_src_var.get()
        if not sel_src:
            messagebox.showerror("", "Select a source group."); return
        src_grp = next((g for g in self.groups if g["name"] in sel_src), None)
        if not src_grp: return

        whole = self.b_whole_group_var.get()
        added = 0

        def make_route(tid, ttitle):
            # Skip duplicates
            if any(r["src_group_id"] == src_grp["id"] and r["src_topic_id"] == tid
                   for r in self._b_routes):
                return None
            dest, conf = self._builder_match_dest(ttitle)
            return {
                "src_group_id": src_grp["id"], "src_group_name": src_grp["name"],
                "src_topic_id": tid, "src_topic_title": ttitle,
                "dest_group_id": self._b_dest_group_id,
                "dest_topic_id": dest["id"] if dest else None,
                "dest_topic_title": dest["title"] if dest else None,
                "dest_topic_action": "use",
                "accepted": conf == "exact",
                "confidence": conf
            }

        if whole:
            r = make_route(1, f"{src_grp['name']} (whole group)")
            if r: self._b_routes.append(r); added += 1
        elif add_all:
            if not self._b_src_topics:
                messagebox.showerror("", "Load source topics first."); return
            for t in self._b_src_topics:
                r = make_route(t["id"], t["title"])
                if r: self._b_routes.append(r); added += 1
        else:
            sel = self.b_src_topic_var.get()
            if not sel:
                messagebox.showerror("", "Select a source topic or use 'Add ALL'."); return
            t = self._builder_parse_topic(sel, self._b_src_topics)
            if not t: messagebox.showerror("", "Could not parse topic."); return
            r = make_route(t["id"], t["title"])
            if r: self._b_routes.append(r); added += 1

        if added == 0:
            messagebox.showinfo("", "No new routes added — already in list."); return
        self._log_s(f"Added {added} route(s) from {src_grp['name']}")
        self._builder_redraw()

    def _builder_accept_suggestions(self):
        count = 0
        for r in self._b_routes:
            if r.get("confidence") == "suggested" and r.get("dest_topic_id"):
                r["accepted"] = True; count += 1
        self._builder_redraw()
        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=f"Accepted {count} suggestion(s).", fg=SUCCESS if count else MUTED)

    def _builder_remove_selected(self):
        routes = self._builder_selected_routes()
        if not routes:
            messagebox.showinfo("", "Select one or more source-topic rows first."); return
        if messagebox.askyesno("Delete selected", f"Delete {len(routes)} source topic(s) from this project?"):
            for route in routes:
                if route in self._b_routes:
                    self._b_routes.remove(route)
            self._builder_redraw()

    def _builder_clear(self):
        if self._b_routes and messagebox.askyesno("Clear", "Remove ALL routes from this project?"):
            self._b_routes.clear()
            self._builder_redraw()

    def _builder_selected_route(self):
        routes = self._builder_selected_routes()
        return routes[0] if routes else None

    def _builder_selected_routes(self):
        if not hasattr(self, "b_routes_tree"):
            return []
        sel = self.b_routes_tree.selection()
        if not sel:
            return []
        route_map = getattr(self, "_b_tree_routes", {})
        routes = []
        for item in sel:
            route = route_map.get(item)
            if route and route not in routes:
                routes.append(route)
        return routes

    def _builder_tree_mousewheel(self, event):
        if hasattr(self, "b_routes_tree"):
            self.b_routes_tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _builder_use_existing_selected(self):
        route = self._builder_selected_route()
        if not route:
            messagebox.showinfo("", "Select a source route first."); return
        self._builder_choose_existing(route)

    def _builder_create_for_selected(self):
        route = self._builder_selected_route()
        if not route:
            messagebox.showinfo("", "Select a source route first."); return
        if not self._b_dest_group_id:
            messagebox.showerror("", "Load destination topics first."); return
        title = simpledialog.askstring(
            "Create destination topic",
            "New destination topic name:",
            initialvalue=route.get("src_topic_title", ""),
            parent=self
        )
        if not title:
            return
        self._b_pending_create_route = route
        ai = self.api_id_var.get(); ah = self.api_hash_var.get()
        threading.Thread(target=lambda: create_topic_in_group(
            int(ai), ah, self._b_dest_group_id, title.strip(),
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._builder_chk_created_for_route)

    def _builder_chk_created_for_route(self):
        try:
            r = self.result_queue.get_nowait()
            route = getattr(self, "_b_pending_create_route", None)
            self._b_pending_create_route = None
            if r and route:
                self._b_dest_topics.append(r)
                route["dest_topic_id"] = r["id"]
                route["dest_topic_title"] = r["title"]
                route["dest_topic_action"] = "create"
                route["accepted"] = True
                route["confidence"] = "manual"
                self._log_s(f"Topic '{r['title']}' created and assigned.")
                self._builder_redraw()
            else:
                messagebox.showerror("Error", "Could not create topic.")
        except queue.Empty:
            self.after(500, self._builder_chk_created_for_route)

    def _builder_choose_existing(self, route):
        d = tk.Toplevel(self)
        d.title("Use Existing")
        d.configure(bg=BG2)
        d.geometry("500x260")
        d.resizable(False, False)
        d.grab_set()

        src_txt = f"{route['src_group_name']} / {route['src_topic_title']}"
        tk.Label(d, text="Source topic", bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=16, pady=(16,0))
        tk.Label(d, text=src_txt, bg=BG2, fg=TEXT, font=FONT_B).pack(anchor="w", padx=16, pady=(0,12))

        suggested = route.get("dest_topic_title") if route.get("confidence") == "suggested" else ""
        if suggested:
            tk.Label(d, text=f"Suggested: {suggested}", bg=BG2, fg=WARNING, font=FONT_SM).pack(anchor="w", padx=16, pady=(0,8))

        tk.Label(d, text="Use existing destination topic:", bg=BG2, fg=TEXT, font=FONT).pack(anchor="w", padx=16, pady=(0,4))
        dest_var = tk.StringVar(value=route.get("dest_topic_title") or suggested)
        cb = ttk.Combobox(d, textvariable=dest_var, width=44, state="readonly")
        cb["values"] = [t["title"] for t in self._b_dest_topics]
        cb.pack(padx=16, pady=(0,16))

        def _use():
            sel = dest_var.get()
            t = next((t for t in self._b_dest_topics if t["title"] == sel), None)
            if not t: messagebox.showerror("", "Select a destination topic."); return
            old = route.get("dest_topic_title")
            route["dest_topic_id"] = t["id"]
            route["dest_topic_title"] = t["title"]
            route["dest_topic_action"] = "use"
            route["accepted"] = True
            if t["title"] != old or route.get("confidence") != "exact":
                route["confidence"] = "manual"
            self._builder_redraw(); d.destroy()

        br = tk.Frame(d, bg=BG2); br.pack(pady=4)
        ttk.Button(br, text="Use Existing", style="P.TButton", command=_use).pack(side=tk.LEFT, padx=4)
        ttk.Button(br, text="Cancel", style="G.TButton", command=d.destroy).pack(side=tk.LEFT, padx=4)
        d.bind("<Return>", lambda e: _use())

    def _builder_redraw(self):
        if not hasattr(self, 'b_routes_tree'):
            return

        tree = self.b_routes_tree
        for item in tree.get_children():
            tree.delete(item)
        self._b_tree_routes = {}

        unassigned = [r for r in self._b_routes if not r.get("dest_topic_id")]
        assigned   = [r for r in self._b_routes if r.get("dest_topic_id")]

        buckets = {}
        for r in assigned:
            key = (r["dest_topic_title"], r.get("dest_topic_id"))
            buckets.setdefault(key, []).append(r)
        if unassigned:
            buckets[("Needs destination", None)] = unassigned

        if not self._b_routes:
            tree.insert("", "end", text="No routes yet", values=("Add source topics above", "", ""))
        else:
            for (title, tid), routes in sorted(buckets.items(), key=lambda x: (x[0][0] != "Needs destination", x[0][0].lower())):
                parent = tree.insert("", "end", text=title, open=True,
                                     values=("", f"{len(routes)} source(s)", ""))
                for r in sorted(routes, key=lambda x: (x["src_group_name"].lower(), x["src_topic_title"].lower())):
                    if not r.get("dest_topic_id"):
                        dest = ""
                        status = "Choose destination"
                    elif r.get("accepted"):
                        dest = r.get("dest_topic_title", "")
                        status = "Ready"
                    elif r.get("confidence") == "suggested":
                        dest = r.get("dest_topic_title", "")
                        status = "Suggestion"
                    else:
                        dest = r.get("dest_topic_title", "")
                        status = "Review"
                    iid = tree.insert(parent, "end",
                                      text="",
                                      values=(f"{r['src_group_name']} / {r['src_topic_title']}", dest, status))
                    self._b_tree_routes[iid] = r

        # Update status label
        total = len(self._b_routes)
        accepted = sum(1 for r in self._b_routes if r.get("accepted"))
        n_unassigned = len(unassigned)
        pending = total - accepted - n_unassigned

        if total == 0:
            status = "No routes yet — add sources above."
            color = MUTED
        elif n_unassigned > 0:
            status = f"{n_unassigned} route(s) need a destination."
            color = DANGER
        elif pending > 0:
            status = f"{pending} suggestion(s) need approval."
            color = WARNING
        else:
            status = f"All {total} route(s) ready to save."
            color = SUCCESS

        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=status, fg=color)

    def _builder_load_project(self):
        selected = self.b_existing_var.get()
        name = getattr(self, "_b_project_display_to_name", {}).get(selected, selected)
        if not name: messagebox.showinfo("", "Select a project to load."); return
        p = next((x for x in self.config_data["projects"] if x["name"] == name), None)
        if not p: return
        if p.get("mode") != "routes":
            messagebox.showinfo("Classic project",
                "This project can be run or deleted, but it cannot be edited in Project Builder.")
            return
        self.b_name_var.set(p["name"])
        self._b_routes = [dict(r) for r in p.get("routes", [])]
        if self._b_routes:
            r0 = self._b_routes[0]
            self._b_dest_group_id = r0.get("dest_group_id")
            grp = next((g for g in self.groups if g["id"] == self._b_dest_group_id), None)
            if grp:
                self.b_dest_var.set(f"{grp['name']}  (id: {grp['id']})")
                self._b_dest_group_name = grp["name"]
        # Rebuild dest_topics from routes
        seen = {}
        for r in self._b_routes:
            if r.get("dest_topic_id") and r["dest_topic_id"] not in seen:
                seen[r["dest_topic_id"]] = {"id": r["dest_topic_id"], "title": r["dest_topic_title"]}
        self._b_dest_topics = list(seen.values())
        self._builder_redraw()
        if hasattr(self, 'b_dest_status'):
            self.b_dest_status.config(
                text=f"Loaded from project — {len(self._b_dest_topics)} dest topics known.", fg=ACCENT)
        # Restore filter checkboxes if saved
        f = p.get("filters", {})
        if hasattr(self, 'b_f_msg'):
            self.b_f_msg.set(f.get("messages", True))
            self.b_f_img.set(f.get("images", True))
            self.b_f_fil.set(f.get("files", True))
            self.b_f_skip_dup.set(f.get("skip_duplicates", True))
        self._log_s(f"Project '{name}' loaded: {len(self._b_routes)} routes")

    def _builder_delete_project(self):
        selected = self.b_existing_var.get()
        name = getattr(self, "_b_project_display_to_name", {}).get(selected, selected)
        if not name:
            messagebox.showinfo("", "Select a project to delete."); return
        idx = next((i for i, p in enumerate(self.config_data.get("projects", [])) if p.get("name") == name), None)
        if idx is None:
            return
        project = self.config_data["projects"][idx]
        if not messagebox.askyesno("Delete project",
            f"Delete '{project.get('name')}'?\n\nProgress/state files will be kept."):
            return
        self.config_data["projects"].pop(idx)
        save_config(self.config_data)
        if self.b_name_var.get().strip() == name:
            self.b_name_var.set("")
            self._b_routes = []
            self._b_dest_topics = []
            self._b_src_topics = []
            self._builder_redraw()
        self.b_existing_var.set("")
        self._builder_refresh_project_combo()
        self._download_refresh_project_combo()
        self._refresh_run_dd()
        self._log_s(f"Project '{name}' deleted. State file kept.")

    def _builder_save_project(self):
        name = self.b_name_var.get().strip()
        if not name: messagebox.showerror("", "Enter a project name."); return
        if not self._b_routes:
            messagebox.showerror("", "No routes to save. Add sources first."); return
        unassigned = [r for r in self._b_routes if not r.get("dest_topic_id")]
        if unassigned:
            messagebox.showerror("Unassigned routes",
                f"{len(unassigned)} route(s) have no destination.\n"
                "Use Existing or Create before saving."); return
        unaccepted = [r for r in self._b_routes if not r.get("accepted")]
        if unaccepted:
            messagebox.showerror("Suggestions need approval",
                f"{len(unaccepted)} suggestion(s) still need approval.\n"
                "Choose Accept Suggestions, Use Existing, or Create before saving."); return

        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        dest_name = self._b_dest_group_name or (self._b_routes[0]["dest_group_id"] if self._b_routes else "?")
        src_names = ", ".join(sorted(set(r["src_group_name"] for r in self._b_routes)))

        existing = next((p for p in self.config_data["projects"] if p["name"] == name), None)
        if existing:
            old_map = {f"{r['src_group_id']}_{r['src_topic_id']}": r.get("dest_topic_id")
                       for r in existing.get("routes", [])}
            changed = [r["src_topic_title"] for r in self._b_routes
                       if old_map.get(f"{r['src_group_id']}_{r['src_topic_id']}") not in
                       (None, r.get("dest_topic_id"))]
            if changed:
                if not messagebox.askyesno("Destination changed",
                    f"These routes changed destination:\n{', '.join(changed[:5])}\n\n"
                    "This may cause duplicates. Continue?"): return
            existing["routes"] = self._b_routes
            existing["source_name"] = src_names
            existing["dest_name"] = dest_name
            existing["filters"] = {
                "messages": self.b_f_msg.get(),
                "images":   self.b_f_img.get(),
                "files":    self.b_f_fil.get(),
                "skip_duplicates": self.b_f_skip_dup.get()
            }
        else:
            self.config_data["projects"].append({
                "name": name, "mode": "routes",
                "source_name": src_names, "dest_name": dest_name,
                "routes": self._b_routes,
                "state_file": f"state_{safe}.json",
                "filters": {
                    "messages": self.b_f_msg.get(),
                    "images":   self.b_f_img.get(),
                    "files":    self.b_f_fil.get(),
                    "skip_duplicates": self.b_f_skip_dup.get()
                }
            })
        save_config(self.config_data)
        self._refresh_proj_list()
        self._refresh_run_dd()
        self._builder_refresh_project_combo()
        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=f"✓  Project '{name}' saved.", fg=SUCCESS)
        self._log_s(f"Project '{name}' saved — {len(self._b_routes)} routes.")



    def _auto(self, parent):
        _,inner=make_scrollable(parent)

        cg=card(inner,"Create destination group  (optional)"); cg.pack(fill=tk.X,padx=20,pady=(20,8))
        tk.Label(cg,text="Creates a supergroup and enables Topics. Skip if destination already exists.",
                 bg=BG2,fg=MUTED,font=FONT_SM,justify="left").pack(anchor="w",pady=(0,8))
        r=tk.Frame(cg,bg=BG2); r.pack(fill=tk.X)
        tk.Label(r,text="Group name:",bg=BG2,fg=MUTED,font=FONT_SM,width=14,anchor="w").pack(side=tk.LEFT)
        self.new_group_name=tk.StringVar()
        ttk.Entry(r,textvariable=self.new_group_name,width=28).pack(side=tk.LEFT,padx=(0,10))
        ttk.Button(r,text="Create + Enable Topics",style="P.TButton",command=self._create_group).pack(side=tk.LEFT)

        ap=card(inner,"Add auto backup project"); ap.pack(fill=tk.X,padx=20,pady=8)
        self.proj_name_var=tk.StringVar()
        field_row(ap,"Project name",self.proj_name_var,30)
        self.source_var=tk.StringVar()
        self.source_combo=combo_row(ap,"Source group",self.source_var)
        self.dest_var=tk.StringVar()
        self.dest_combo=combo_row(ap,"Destination group",self.dest_var)

        # Filter options
        flt=tk.Frame(ap,bg=BG2); flt.pack(fill=tk.X,pady=(10,0))
        tk.Label(flt,text="Copy:",bg=BG2,fg=MUTED,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        self.f_msg=tk.BooleanVar(value=True); self.f_img=tk.BooleanVar(value=True); self.f_fil=tk.BooleanVar(value=True)
        for var,lbl in [(self.f_msg,"Text messages"),(self.f_img,"Images"),(self.f_fil,"Files")]:
            tk.Checkbutton(flt,text=lbl,variable=var,bg=BG2,fg=TEXT,activebackground=BG2,
                           activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        flt2=tk.Frame(ap,bg=BG2); flt2.pack(fill=tk.X,pady=(4,0))
        self.f_skip_dup=tk.BooleanVar(value=True)
        tk.Checkbutton(flt2,text="Skip duplicate files (same name + size)",variable=self.f_skip_dup,
                       bg=BG2,fg=TEXT,activebackground=BG2,activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(anchor="w")

        br=tk.Frame(ap,bg=BG2); br.pack(fill=tk.X,pady=(12,0))
        ttk.Button(br,text="Add Project",style="P.TButton",command=self._add_project).pack(side=tk.LEFT)
        ttk.Button(br,text="↺  Refresh groups",command=self._load_groups).pack(side=tk.LEFT,padx=10)

        sp=card(inner,"Saved auto projects"); sp.pack(fill=tk.X,padx=20,pady=(8,20))
        lf=tk.Frame(sp,bg=BG2); lf.pack(fill=tk.X)
        sb2=ttk.Scrollbar(lf); sb2.pack(side=tk.RIGHT,fill=tk.Y)
        self.proj_list=tk.Listbox(lf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                   bd=0,height=5,yscrollcommand=sb2.set)
        self.proj_list.pack(fill=tk.X); sb2.config(command=self.proj_list.yview)
        br2=tk.Frame(sp,bg=BG2); br2.pack(anchor="w",pady=(8,0))
        ttk.Button(br2,text="Rename",style="G.TButton",command=self._rename_project).pack(side=tk.LEFT)
        ttk.Button(br2,text="Delete",style="G.TButton",command=self._del_project).pack(side=tk.LEFT,padx=8)
        self._refresh_proj_list()

    def _refresh_combos(self):
        names=[f"{g['name']}  (id: {g['id']})" for g in self.groups]
        if hasattr(self, 'source_combo'): self.source_combo["values"]=names; self.dest_combo["values"]=names
        if hasattr(self,'m_src_combo'): self.m_src_combo["values"]=names; self.m_dst_combo["values"]=names
        if hasattr(self,'b_src_combo'): self.b_src_combo["values"]=names; self.b_dest_combo["values"]=names
        if hasattr(self,'d_src_combo'): self.d_src_combo["values"]=names

    def _create_group(self):
        if self._telegram_busy("create a group"):
            return
        title=self.new_group_name.get().strip()
        if not title: messagebox.showerror("Missing","Enter a name."); return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        threading.Thread(target=lambda:create_group(int(ai),ah,title,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_create_group)

    def _chk_create_group(self):
        try:
            r=self.result_queue.get_nowait()
            if r:
                self.groups.append(r); self._refresh_combos()
                if hasattr(self, 'dest_var'):
                    self.dest_var.set(f"{r['name']}  (id: {r['id']})")
                if hasattr(self, 'b_dest_var'):
                    self.b_dest_var.set(f"{r['name']}  (id: {r['id']})")
                    self._b_dest_group_id = r["id"]
                    self._b_dest_group_name = r["name"]
                    if hasattr(self, 'b_dest_status'):
                        self.b_dest_status.config(text="Group created. Loading its topics...", fg=SUCCESS)
                    self.after(300, self._builder_load_dest_topics)
                messagebox.showinfo("Done",f"'{r['name']}' created with Topics enabled!")
                self.new_group_name.set("")
            else: messagebox.showerror("Error","Failed — check log.")
        except queue.Empty: self.after(500,self._chk_create_group)

    def _add_project(self):
        name=self.proj_name_var.get().strip(); src_s=self.source_var.get(); dst_s=self.dest_var.get()
        if not name or not src_s or not dst_s: messagebox.showerror("Missing","Fill in all fields."); return
        src=next((g for g in self.groups if g["name"] in src_s),None)
        dst=next((g for g in self.groups if g["name"] in dst_s),None)
        if not src or not dst: messagebox.showerror("Error","Can't match groups. Refresh."); return
        safe=re.sub(r'[^a-zA-Z0-9_]','_',name)
        self.config_data["projects"].append({
            "name":name,"mode":"auto",
            "source_id":src["id"],"source_name":src["name"],
            "dest_id":dst["id"],"dest_name":dst["name"],
            "state_file":f"state_{safe}.json",
            "filters":{"messages":self.f_msg.get(),"images":self.f_img.get(),
                       "files":self.f_fil.get(),"skip_duplicates":self.f_skip_dup.get()}
        })
        save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd()
        self.proj_name_var.set("")

    def _rename_project(self):
        sel=self.proj_list.curselection()
        if not sel: messagebox.showinfo("","Select a project."); return
        p=self.config_data["projects"][sel[0]]
        d=tk.Toplevel(self); d.title("Rename"); d.configure(bg=BG2)
        d.geometry("360x140"); d.resizable(False,False); d.grab_set()
        tk.Label(d,text="New name:",bg=BG2,fg=TEXT,font=FONT).pack(pady=(20,6))
        nv=tk.StringVar(value=p["name"])
        e=ttk.Entry(d,textvariable=nv,width=32); e.pack(pady=(0,12)); e.select_range(0,tk.END); e.focus()
        def _ok():
            nn=nv.get().strip()
            if not nn: return
            old_sf=p["state_file"]; safe=re.sub(r'[^a-zA-Z0-9_]','_',nn); new_sf=f"state_{safe}.json"
            if old_sf!=new_sf and os.path.exists(old_sf):
                try: os.rename(old_sf,new_sf)
                except Exception as ex: messagebox.showerror("Error",str(ex)); return
            p["name"]=nn; p["state_file"]=new_sf
            save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd(); d.destroy()
        ttk.Button(d,text="Rename",style="P.TButton",command=_ok).pack()
        d.bind("<Return>",lambda e:_ok())

    def _del_project(self):
        sel=self.proj_list.curselection()
        if not sel: return
        if messagebox.askyesno("Delete","Delete project? (state file kept)"):
            self.config_data["projects"].pop(sel[0]); save_config(self.config_data)
            self._refresh_proj_list(); self._refresh_run_dd()

    def _refresh_proj_list(self):
        if not hasattr(self, 'proj_list'):
            return
        self.proj_list.delete(0,tk.END)
        for p in self.config_data.get("projects",[]):
            mode=f" [{p.get('mode','auto')}]"
            src=p.get('source_name','?'); dst=p.get('dest_name','?')
            self.proj_list.insert(tk.END,f"  {p['name']}{mode}   ·   {src}  →  {dst}")

    # ── MANUAL MAP ─────────────────────────────────────────────────────────────
    def _manual(self, parent):
        _,inner=make_scrollable(parent)
        info=card(inner,"Manual topic mapping"); info.pack(fill=tk.X,padx=20,pady=(20,8))
        tk.Label(info,text="Map individual topics from any source group to any destination topic.\n"
                           "Source groups without topics are treated as a single channel (General).\n"
                           "You can mix topics from different source groups into one destination.",
                 bg=BG2,fg=MUTED,font=FONT_SM,justify="left").pack(anchor="w",pady=(0,4))

        sp=card(inner,"Source"); sp.pack(fill=tk.X,padx=20,pady=(0,6))
        r1=tk.Frame(sp,bg=BG2); r1.pack(fill=tk.X,pady=3)
        tk.Label(r1,text="Source group",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_src_var=tk.StringVar()
        self.m_src_combo=ttk.Combobox(r1,textvariable=self.m_src_var,width=36,state="readonly"); self.m_src_combo.pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r1,text="Load Topics",command=self._load_src_topics).pack(side=tk.LEFT)
        r2=tk.Frame(sp,bg=BG2); r2.pack(fill=tk.X,pady=3)
        tk.Label(r2,text="Source topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_src_topic_var=tk.StringVar()
        self.m_src_topic_combo=ttk.Combobox(r2,textvariable=self.m_src_topic_var,width=36,state="readonly"); self.m_src_topic_combo.pack(side=tk.LEFT)

        dp=card(inner,"Destination"); dp.pack(fill=tk.X,padx=20,pady=(0,6))
        r3=tk.Frame(dp,bg=BG2); r3.pack(fill=tk.X,pady=3)
        tk.Label(r3,text="Dest group",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_dst_var=tk.StringVar()
        self.m_dst_combo=ttk.Combobox(r3,textvariable=self.m_dst_var,width=36,state="readonly"); self.m_dst_combo.pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r3,text="Load Topics",command=self._load_dst_topics).pack(side=tk.LEFT)
        r4=tk.Frame(dp,bg=BG2); r4.pack(fill=tk.X,pady=3)
        tk.Label(r4,text="Dest topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_dst_topic_var=tk.StringVar()
        self.m_dst_topic_combo=ttk.Combobox(r4,textvariable=self.m_dst_topic_var,width=36,state="readonly"); self.m_dst_topic_combo.pack(side=tk.LEFT,padx=(0,8))
        r5=tk.Frame(dp,bg=BG2); r5.pack(fill=tk.X,pady=3)
        tk.Label(r5,text="Or create topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_new_topic_var=tk.StringVar()
        ttk.Entry(r5,textvariable=self.m_new_topic_var,width=26).pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r5,text="Create Topic",command=self._create_dest_topic).pack(side=tk.LEFT)

        # Filters for manual mode
        fp=card(inner,"Filters"); fp.pack(fill=tk.X,padx=20,pady=(0,6))
        frow=tk.Frame(fp,bg=BG2); frow.pack(fill=tk.X)
        tk.Label(frow,text="Copy:",bg=BG2,fg=MUTED,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        self.mf_msg=tk.BooleanVar(value=True); self.mf_img=tk.BooleanVar(value=True); self.mf_fil=tk.BooleanVar(value=True)
        for var,lbl in [(self.mf_msg,"Text"),(self.mf_img,"Images"),(self.mf_fil,"Files")]:
            tk.Checkbutton(frow,text=lbl,variable=var,bg=BG2,fg=TEXT,activebackground=BG2,
                           activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(side=tk.LEFT,padx=(0,10))
        self.mf_skip_dup=tk.BooleanVar(value=True)
        tk.Checkbutton(fp,text="Skip duplicate files",variable=self.mf_skip_dup,
                       bg=BG2,fg=TEXT,activebackground=BG2,activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(anchor="w",pady=(4,0))

        ar=tk.Frame(inner,bg=BG); ar.pack(fill=tk.X,padx=20,pady=4)
        ttk.Button(ar,text="+ Add this mapping",style="P.TButton",command=self._add_mapping).pack(side=tk.LEFT)

        ml=card(inner,"Mappings in this project"); ml.pack(fill=tk.X,padx=20,pady=(0,8))
        lf=tk.Frame(ml,bg=BG2); lf.pack(fill=tk.X)
        sb3=ttk.Scrollbar(lf); sb3.pack(side=tk.RIGHT,fill=tk.Y)
        self.m_list=tk.Listbox(lf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                bd=0,height=5,yscrollcommand=sb3.set)
        self.m_list.pack(fill=tk.X); sb3.config(command=self.m_list.yview)
        ttk.Button(ml,text="Remove selected",style="G.TButton",command=self._remove_mapping).pack(anchor="w",pady=(8,0))

        sr=card(inner,"Save as project"); sr.pack(fill=tk.X,padx=20,pady=(0,20))
        row=tk.Frame(sr,bg=BG2); row.pack(fill=tk.X)
        tk.Label(row,text="Project name:",bg=BG2,fg=MUTED,font=FONT_SM,width=14,anchor="w").pack(side=tk.LEFT)
        self.m_proj_name=tk.StringVar()
        ttk.Entry(row,textvariable=self.m_proj_name,width=28).pack(side=tk.LEFT,padx=(0,10))
        ttk.Button(row,text="Save Project",style="P.TButton",command=self._save_manual_project).pack(side=tk.LEFT)

    def _load_src_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel=self.m_src_var.get()
        if not sel: messagebox.showerror("","Select a source group."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        self._log_s(f"Loading topics from '{grp['name']}'...")
        threading.Thread(target=lambda:list_topics(int(ai),ah,grp["id"],self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,lambda:self._chk_topics("src"))

    def _load_dst_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel=self.m_dst_var.get()
        if not sel: messagebox.showerror("","Select a destination group."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        self._log_s(f"Loading topics from '{grp['name']}'...")
        threading.Thread(target=lambda:list_topics(int(ai),ah,grp["id"],self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,lambda:self._chk_topics("dst"))

    def _chk_topics(self, which):
        try:
            r=self.result_queue.get_nowait()
            if isinstance(r,list):
                if which=="src":
                    self._m_src_topics=r
                    self.m_src_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in r]
                    self._log_s(f"Loaded {len(r)} source topics")
                else:
                    self._m_dst_topics=r
                    self.m_dst_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in r]
                    self._log_s(f"Loaded {len(r)} dest topics")
            elif r is None:
                self._log_s("Topic load failed. If a backup is running, wait until it finishes and try again.")
        except queue.Empty: self.after(500,lambda:self._chk_topics(which))

    def _create_dest_topic(self):
        if self._telegram_busy("create a topic"):
            return
        title=self.m_new_topic_var.get().strip()
        if not title: messagebox.showerror("","Enter a topic name."); return
        sel=self.m_dst_var.get()
        if not sel: messagebox.showerror("","Select destination group first."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        threading.Thread(target=lambda:create_topic_in_group(int(ai),ah,grp["id"],title,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_new_topic)

    def _chk_new_topic(self):
        try:
            r=self.result_queue.get_nowait()
            if r:
                self._m_dst_topics.append(r)
                self.m_dst_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in self._m_dst_topics]
                self.m_dst_topic_var.set(f"{r['title']}  (id:{r['id']})")
                self.m_new_topic_var.set("")
            else: messagebox.showerror("Error","Could not create topic.")
        except queue.Empty: self.after(500,self._chk_new_topic)

    def _add_mapping(self):
        sg=self.m_src_var.get(); st=self.m_src_topic_var.get()
        dg=self.m_dst_var.get(); dt=self.m_dst_topic_var.get()
        if not all([sg,st,dg,dt]): messagebox.showerror("Incomplete","Select source group, source topic, dest group and dest topic."); return
        src_grp=next((g for g in self.groups if g["name"] in sg),None)
        dst_grp=next((g for g in self.groups if g["name"] in dg),None)
        src_t=next((t for t in self._m_src_topics if t["title"] in st),None)
        dst_t=next((t for t in self._m_dst_topics if t["title"] in dt),None)
        if not all([src_grp,dst_grp,src_t,dst_t]): messagebox.showerror("Error","Reload topics and try again."); return
        self._manual_mappings.append({
            "src_group_id":src_grp["id"],"src_group_name":src_grp["name"],
            "src_topic_id":src_t["id"],"src_topic_title":src_t["title"],
            "dest_group_id":dst_grp["id"],"dest_group_name":dst_grp["name"],
            "dest_topic_id":dst_t["id"],"dest_topic_title":dst_t["title"]
        })
        self.m_list.insert(tk.END,f"  {src_grp['name']} / {src_t['title']}  →  {dst_grp['name']} / {dst_t['title']}")

    def _remove_mapping(self):
        sel=self.m_list.curselection()
        if not sel: return
        self._manual_mappings.pop(sel[0]); self.m_list.delete(sel[0])

    def _save_manual_project(self):
        if not self._manual_mappings: messagebox.showerror("","Add at least one mapping."); return
        name=self.m_proj_name.get().strip()
        if not name: messagebox.showerror("","Enter a project name."); return
        safe=re.sub(r'[^a-zA-Z0-9_]','_',name)
        dst_name=self._manual_mappings[0]["dest_group_name"]
        src_names=", ".join(set(m["src_group_name"] for m in self._manual_mappings))
        self.config_data["projects"].append({
            "name":name,"mode":"manual",
            "source_name":src_names,"dest_name":dst_name,
            "mappings":self._manual_mappings.copy(),
            "state_file":f"state_{safe}.json",
            "filters":{"messages":self.mf_msg.get(),"images":self.mf_img.get(),
                       "files":self.mf_fil.get(),"skip_duplicates":self.mf_skip_dup.get()}
        })
        save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd()
        messagebox.showinfo("Saved",f"'{name}' saved with {len(self._manual_mappings)} mapping(s).")
        self._manual_mappings.clear(); self.m_list.delete(0,tk.END); self.m_proj_name.set("")

    # ── DOWNLOAD ────────────────────────────────────────────────────────────────
    def _download_tab(self, parent):
        _, inner = make_scrollable(parent)
        top = card(inner, "Download source group"); top.pack(fill=tk.X, padx=20, pady=(20,8))
        tk.Label(top, text="Choose a Telegram group, then download files into folders by group and topic.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))

        name_row = tk.Frame(top, bg=BG2); name_row.pack(fill=tk.X, pady=3)
        tk.Label(name_row, text="Project name:", bg=BG2, fg=TEXT, font=FONT, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.d_name_var, width=36).pack(side=tk.LEFT)

        edit_row = tk.Frame(top, bg=BG2); edit_row.pack(fill=tk.X, pady=3)
        tk.Label(edit_row, text="Edit existing:", bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_existing_var = tk.StringVar()
        self.d_existing_combo = ttk.Combobox(edit_row, textvariable=self.d_existing_var, width=36, state="readonly")
        self.d_existing_combo.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(edit_row, text="Load Download Project", command=self._download_load_project).pack(side=tk.LEFT)
        self._download_refresh_project_combo()

        r1 = tk.Frame(top, bg=BG2); r1.pack(fill=tk.X, pady=3)
        tk.Label(r1, text="Source group:", bg=BG2, fg=TEXT, font=FONT, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_src_var = tk.StringVar()
        self.d_src_combo = ttk.Combobox(r1, textvariable=self.d_src_var, width=42, state="readonly")
        self.d_src_combo.pack(side=tk.LEFT, padx=(0,8))
        self.d_src_combo.bind("<<ComboboxSelected>>", lambda e: self._download_load_topics())
        ttk.Button(r1, text="Reload Topics", command=self._download_load_topics).pack(side=tk.LEFT)

        opts = card(inner, "What to download"); opts.pack(fill=tk.X, padx=20, pady=8)
        self.d_files_var = tk.BooleanVar(value=True)
        self.d_photos_var = tk.BooleanVar(value=False)
        self.d_videos_var = tk.BooleanVar(value=False)
        self.d_text_var = tk.BooleanVar(value=False)
        for var, label in [
            (self.d_files_var, "Files"),
            (self.d_photos_var, "Photos"),
            (self.d_videos_var, "Videos"),
            (self.d_text_var, "Text messages to messages.txt"),
        ]:
            tk.Checkbutton(opts, text=label, variable=var, bg=BG2, fg=TEXT,
                           activebackground=BG2, activeforeground=TEXT,
                           selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,16))
        tk.Label(opts, text="GIFs are skipped.", bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(anchor="w", pady=(8,0))

        topics = card(inner, "Topics"); topics.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
        row = tk.Frame(topics, bg=BG2); row.pack(fill=tk.X, pady=(0,8))
        ttk.Button(row, text="Select Whole Group / All Topics", command=self._download_select_all).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(row, text="Calculate Size", style="P.TButton", command=self._download_calculate).pack(side=tk.LEFT)
        self.d_status_lbl = tk.Label(row, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.d_status_lbl.pack(side=tk.LEFT, padx=12)

        search_row = tk.Frame(topics, bg=BG2); search_row.pack(fill=tk.X, pady=(0,8))
        tk.Label(search_row, text="Search topics:", bg=BG2, fg=MUTED, font=FONT_SM).pack(side=tk.LEFT, padx=(0,8))
        self.d_topic_search_var = tk.StringVar()
        self.d_topic_search_var.trace_add("write", lambda *_: self._download_filter_topics())
        ttk.Entry(search_row, textvariable=self.d_topic_search_var, width=36).pack(side=tk.LEFT)

        lf = tk.Frame(topics, bg=BG2); lf.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf); sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.d_topic_list = tk.Listbox(lf, bg=BG3, fg=TEXT, font=FONT, selectbackground=ACCENT,
                                       selectforeground=BG, bd=0, height=10, selectmode=tk.EXTENDED,
                                       yscrollcommand=sb.set)
        self.d_topic_list.pack(fill=tk.BOTH, expand=True); sb.config(command=self.d_topic_list.yview)

        dest = card(inner, "Destination folder"); dest.pack(fill=tk.X, padx=20, pady=8)
        r2 = tk.Frame(dest, bg=BG2); r2.pack(fill=tk.X)
        self.d_folder_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.d_folder_var, width=58).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r2, text="Choose Folder", command=self._download_choose_folder).pack(side=tk.LEFT)

        controls = card(inner); controls.pack(fill=tk.X, padx=20, pady=(8,20))
        ttk.Button(controls, text="Save Download Project", style="P.TButton",
                   command=self._download_save_project).pack(side=tk.LEFT, padx=(0,10))
        self.d_start_btn = ttk.Button(controls, text="Save and Run", command=self._download_run_now)
        self.d_start_btn.pack(side=tk.LEFT, padx=(0,10))
        self.d_stop_btn = ttk.Button(controls, text="Stop", style="D.TButton", command=self._stop)
        self.d_stop_btn.pack(side=tk.LEFT)
        self.d_stop_btn.state(["disabled"])

        log_card = card(inner, "Download log", pady=12); log_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0,20))
        self.download_log = scrolledtext.ScrolledText(log_card, bg=BG, fg=TEXT, font=MONO,
                                                      insertbackground=ACCENT, bd=0, relief="flat",
                                                      padx=10, pady=8, height=10)
        self.download_log.pack(fill=tk.BOTH, expand=True)
        for tag, color in [("g", SUCCESS), ("r", DANGER), ("y", WARNING), ("d", MUTED),
                           ("a", ACCENT), ("status", "#82b1ff"), ("copied", SUCCESS)]:
            self.download_log.tag_config(tag, foreground=color)

    def _download_filters(self):
        return {
            "files": self.d_files_var.get(),
            "photos": self.d_photos_var.get(),
            "videos": self.d_videos_var.get(),
            "text": self.d_text_var.get(),
        }

    def _download_refresh_project_combo(self):
        projects = [p for p in self.config_data.get("projects", []) if p.get("mode") == "download"]
        projects.sort(key=lambda p: p.get("name", "").lower())
        self._download_display_to_name = {}
        displays = []
        for p in projects:
            display = f"{p.get('source_name','?')} / {p.get('name','?')}"
            self._download_display_to_name[display] = p.get("name")
            displays.append(display)
        if hasattr(self, "d_existing_combo"):
            self.d_existing_combo["values"] = displays

    def _download_load_project(self):
        selected = self.d_existing_var.get()
        name = getattr(self, "_download_display_to_name", {}).get(selected, selected)
        if not name:
            messagebox.showinfo("", "Select a download project to load."); return
        p = next((x for x in self.config_data.get("projects", []) if x.get("name") == name and x.get("mode") == "download"), None)
        if not p:
            return
        self.d_name_var.set(p.get("name", ""))
        src = next((g for g in self.groups if g.get("id") == p.get("source_id")), None)
        self.d_src_var.set(f"{src['name']}  (id: {src['id']})" if src else p.get("source_name", ""))
        self.d_folder_var.set(p.get("output_dir") or p.get("dest_name", ""))
        f = p.get("filters", {})
        self.d_files_var.set(f.get("files", True))
        self.d_photos_var.set(f.get("photos", False))
        self.d_videos_var.set(f.get("videos", False))
        self.d_text_var.set(f.get("text", False))
        self._d_topics = [dict(t) for t in p.get("topics", [])]
        selected_ids = {int(x) for x in p.get("selected_topic_ids", [])}
        self.d_topic_search_var.set("")
        self._download_filter_topics()
        for i, t in enumerate(getattr(self, "_d_topic_view", [])):
            if int(t["id"]) in selected_ids:
                self.d_topic_list.select_set(i)
        self.d_status_lbl.config(text=f"Loaded download project '{p.get('name')}'.", fg=SUCCESS)

    def _download_load_topics(self):
        if self._telegram_busy("load topics"):
            return
        src = self._group_from_selection(self.d_src_var.get())
        if not src: messagebox.showerror("", "Select a source group."); return
        self.d_status_lbl.config(text="Loading topics...", fg=WARNING)
        self._d_topics = []
        self._d_topic_view = []
        self.d_topic_list.delete(0, tk.END)
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            src["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._download_chk_topics)

    def _download_chk_topics(self):
        try:
            r = self.result_queue.get_nowait()
            if isinstance(r, list):
                self._d_topics = r
                self.d_topic_search_var.set("")
                self._download_filter_topics(select_all=True)
                self._download_select_all()
                self.d_status_lbl.config(text=f"{len(r)} topic(s) loaded.", fg=SUCCESS)
            elif r is None:
                self.d_status_lbl.config(text="Could not load topics.", fg=DANGER)
        except queue.Empty:
            self.after(500, self._download_chk_topics)

    def _download_select_all(self):
        if hasattr(self, "d_topic_list"):
            self.d_topic_list.select_set(0, tk.END)

    def _download_selected_topics(self):
        idxs = self.d_topic_list.curselection()
        view = getattr(self, "_d_topic_view", self._d_topics)
        return [view[i] for i in idxs]

    def _download_filter_topics(self, select_all=False):
        if not hasattr(self, "d_topic_list"):
            return
        query = self.d_topic_search_var.get().strip().lower() if hasattr(self, "d_topic_search_var") else ""
        previous_ids = {int(t["id"]) for t in self._download_selected_topics()} if not select_all else set()
        self._d_topic_view = [
            t for t in self._d_topics
            if not query or query in t.get("title", "").lower() or query in str(t.get("id", ""))
        ]
        self.d_topic_list.delete(0, tk.END)
        for t in self._d_topic_view:
            self.d_topic_list.insert(tk.END, self._download_topic_display(t))
        for i, t in enumerate(self._d_topic_view):
            if select_all or int(t["id"]) in previous_ids:
                self.d_topic_list.select_set(i)
        if query:
            self.d_status_lbl.config(text=f"{len(self._d_topic_view)}/{len(self._d_topics)} topic(s) shown.", fg=ACCENT)

    def _download_topic_display(self, topic):
        suffix = topic.get("_download_summary", "")
        return f"{topic['title']}  (id:{topic['id']}){suffix}"

    def _download_calculate(self):
        if self._telegram_busy("calculate download size"):
            return
        src = self._group_from_selection(self.d_src_var.get())
        selected = self._download_selected_topics()
        if not src or not selected: messagebox.showerror("", "Select a source group and at least one topic."); return
        self.d_status_lbl.config(text="Calculating...", fg=WARNING)
        self.stop_event.clear()
        threading.Thread(target=lambda: calculate_download_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(), src["id"],
            self._d_topics, [t["id"] for t in selected], self._download_filters(),
            self.log_queue, self.result_queue, self.stop_event), daemon=True).start()
        self.after(500, self._download_chk_calculation)

    def _download_chk_calculation(self):
        try:
            rows = self.result_queue.get_nowait()
            if rows is None:
                self.d_status_lbl.config(text="Calculation failed.", fg=DANGER); return
            by_id = {int(r["id"]): r for r in rows}
            for t in self._d_topics:
                row = by_id.get(int(t["id"]))
                if row:
                    t["_download_summary"] = f" — {row['count']} item(s), {_fmt_bytes(row['size'])}"
            self._download_filter_topics()
            for i, t in enumerate(getattr(self, "_d_topic_view", [])):
                if int(t["id"]) in by_id:
                    self.d_topic_list.select_set(i)
            total = sum(r["size"] for r in rows)
            count = sum(r["count"] for r in rows)
            self.d_status_lbl.config(text=f"{count} item(s), {_fmt_bytes(total)} selected.", fg=SUCCESS)
        except queue.Empty:
            self.after(500, self._download_chk_calculation)

    def _download_choose_folder(self):
        folder = filedialog.askdirectory(title="Choose download folder")
        if folder:
            self.d_folder_var.set(folder)

    def _download_project_payload(self):
        src = self._group_from_selection(self.d_src_var.get())
        selected = self._download_selected_topics()
        folder = self.d_folder_var.get().strip()
        if not src or not selected: messagebox.showerror("", "Select a source group and at least one topic."); return
        if not folder: messagebox.showerror("", "Choose a destination folder."); return
        if not any(self._download_filters().values()):
            messagebox.showerror("", "Choose at least one thing to download."); return
        name = self.d_name_var.get().strip() or f"Download {src['name']}"
        return {
            "name": name,
            "mode": "download",
            "source_id": src["id"],
            "source_name": src["name"],
            "dest_name": folder,
            "output_dir": folder,
            "topics": [dict(t) for t in self._d_topics],
            "selected_topic_ids": [t["id"] for t in selected],
            "filters": self._download_filters()
        }

    def _download_save_project(self, silent=False):
        payload = self._download_project_payload()
        if not payload:
            return None
        existing = next((p for p in self.config_data.get("projects", []) if p.get("name") == payload["name"]), None)
        if existing:
            existing.update(payload)
        else:
            self.config_data.setdefault("projects", []).append(payload)
        save_config(self.config_data)
        self._refresh_run_dd()
        self._builder_refresh_project_combo()
        self._download_refresh_project_combo()
        self.d_name_var.set(payload["name"])
        if not silent:
            messagebox.showinfo("Saved", f"Download project '{payload['name']}' saved.")
        self._log_d(f"Saved download project: {payload['name']}", "g")
        return payload

    def _download_run_now(self):
        payload = self._download_save_project(silent=True)
        if not payload:
            return
        if hasattr(self, "run_proj_var"):
            self.run_proj_var.set(self._project_label(payload["name"]))
        self.nb.select(self.nb.tabs()[-1])
        self._start()

    def _start_download_project(self, p, ai, ah, filters):
        selected_topics = p.get("selected_topic_ids") or [t.get("id") for t in p.get("topics", [])]
        self.active_task = "download"
        if hasattr(self, "d_start_btn"): self.d_start_btn.state(["disabled"])
        if hasattr(self, "d_stop_btn"): self.d_stop_btn.state(["!disabled"])
        self._log_d("Starting download...", "a")
        self._log_d(f"Source: {p.get('source_name','?')}", "d")
        self._log_d(f"Folder: {p.get('output_dir') or p.get('dest_name','?')}", "d")
        selected_names = [t.get("title", "General") for t in p.get("topics", []) if t.get("id") in selected_topics]
        self._log_d("Topics: " + ", ".join(selected_names[:8]) + ("..." if len(selected_names) > 8 else ""), "d")
        threading.Thread(target=lambda: run_download_topics(
            int(ai), ah, p["source_id"], p.get("source_name", "Source"),
            p.get("output_dir") or p.get("dest_name"), p.get("topics", []), selected_topics, filters,
            self.log_queue, self.stop_event), daemon=True).start()

    def _download_start(self):
        payload = self._download_project_payload()
        if not payload:
            return
        if self.backup_running:
            messagebox.showwarning("Running", "A task is already running."); return
        self.stop_event.clear(); self.backup_running = True
        self.d_start_btn.state(["disabled"]); self.d_stop_btn.state(["!disabled"])
        if hasattr(self, "start_btn"): self.start_btn.state(["disabled"])
        self._start_download_project(payload, self.api_id_var.get(), self.api_hash_var.get(), payload["filters"])

    # ── RUN ────────────────────────────────────────────────────────────────────
    def _run_tab(self, parent):
        _, inner = make_scrollable(parent)
        top=card(inner,"Run project",pady=10); top.pack(fill=tk.X,padx=20,pady=(12,2))
        r1=tk.Frame(top,bg=BG2); r1.pack(fill=tk.X,pady=1)
        tk.Label(r1,text="Project:",bg=BG2,fg=MUTED,font=FONT_SM,width=10,anchor="w").pack(side=tk.LEFT)
        self.run_proj_var=tk.StringVar()
        self.run_proj_dd=ttk.Combobox(r1,textvariable=self.run_proj_var,width=42,state="readonly"); self.run_proj_dd.pack(side=tk.LEFT,padx=(0,10))
        self._refresh_run_dd()
        r2=tk.Frame(top,bg=BG2); r2.pack(fill=tk.X,pady=(6,0))
        self.start_btn=ttk.Button(r2,text="▶   Start",style="P.TButton",command=self._start); self.start_btn.pack(side=tk.LEFT)
        self.stop_btn=ttk.Button(r2,text="■   Stop",style="D.TButton",command=self._stop); self.stop_btn.pack(side=tk.LEFT,padx=10)
        self.stop_btn.state(["disabled"])
        self.prog_lbl=tk.Label(inner,text="",bg=BG,fg=SUCCESS,font=FONT_B)

        qc=card(inner,"Activity queue",pady=8); qc.pack(fill=tk.X,padx=20,pady=(2,0))
        tk.Label(qc,text="The running project stays on top. Add waiting projects below it; they run one after another.",
                 bg=BG2,fg=MUTED,font=FONT_SM).pack(anchor="w",pady=(0,4))
        qrow=tk.Frame(qc,bg=BG2); qrow.pack(fill=tk.X)
        ttk.Button(qrow,text="Add Selected to Queue",command=self._queue_add_selected).pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(qrow,text="Start Queue",style="P.TButton",command=self._queue_start).pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(qrow,text="Move Up",command=lambda:self._queue_move(-1)).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Move Down",command=lambda:self._queue_move(1)).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Remove",style="G.TButton",command=self._queue_remove_selected).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Skip Current",style="G.TButton",command=self._queue_skip_current).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Requeue Current",style="G.TButton",command=self._queue_requeue_current).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Clear Queue",style="G.TButton",command=self._queue_clear).pack(side=tk.LEFT)
        qlf=tk.Frame(qc,bg=BG2); qlf.pack(fill=tk.X,pady=(5,0))
        qsb=ttk.Scrollbar(qlf); qsb.pack(side=tk.RIGHT,fill=tk.Y)
        self.queue_list=tk.Listbox(qlf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                   bd=0,height=4,yscrollcommand=qsb.set)
        self.queue_list.pack(fill=tk.X); qsb.config(command=self.queue_list.yview)

        lc=card(inner,"Live log",pady=10); lc.pack(fill=tk.BOTH,expand=True,padx=20,pady=(4,20))
        self.run_log=scrolledtext.ScrolledText(lc,bg=BG,fg=TEXT,font=MONO,insertbackground=ACCENT,bd=0,relief="flat",padx=10,pady=8,height=34)
        self.run_log.pack(fill=tk.BOTH,expand=True)
        self.run_log.tag_config("g", foreground=SUCCESS)
        self.run_log.tag_config("r", foreground=DANGER)
        self.run_log.tag_config("y", foreground=WARNING)
        self.run_log.tag_config("d", foreground=MUTED)
        self.run_log.tag_config("a", foreground=ACCENT)
        self.run_log.tag_config("w", foreground="#ff9800")   # orange for retries
        self.run_log.tag_config("status",foreground="#82b1ff")
        self.run_log.tag_config("copied",foreground=SUCCESS)
        self._queue_refresh()

    def _refresh_run_dd(self):
        self._run_display_to_name = {}
        displays = []
        for p in self._visible_projects():
            label = self._project_label(p["name"])
            self._run_display_to_name[label] = p["name"]
            displays.append(label)
        self.run_proj_dd["values"]=displays
        if displays:
            self.run_proj_dd.current(0)
        else:
            self.run_proj_var.set("")

    def _run_selected_project_name(self):
        selected = self.run_proj_var.get()
        return getattr(self, "_run_display_to_name", {}).get(selected, selected)

    def _visible_projects(self):
        return list(self.config_data.get("projects", []))

    def _project_kind(self, project):
        if project.get("mode") == "download":
            return "Download"
        return "Copy"

    def _project_label(self, name):
        p = next((x for x in self.config_data.get("projects", []) if x.get("name") == name), None)
        if not p:
            return name
        return f"{self._project_kind(p)}: {name}"

    def _queue_refresh(self):
        if not hasattr(self, "queue_list"):
            return
        valid = {p.get("name") for p in self._visible_projects()}
        cleaned = [name for name in self.activity_queue if name in valid]
        if cleaned != self.activity_queue:
            self.activity_queue = cleaned
            self._queue_save()
        self.queue_list.delete(0, tk.END)
        if self.current_project_name:
            self.queue_list.insert(tk.END, f"Running: {self._project_label(self.current_project_name)}")
        for i, name in enumerate(self.activity_queue, 1):
            self.queue_list.insert(tk.END, f"Waiting {i}. {self._project_label(name)}")

    def _queue_save(self):
        self.config_data["activity_queue"] = self.activity_queue
        save_config(self.config_data)

    def _queue_add_selected(self):
        name = self._run_selected_project_name()
        if not name:
            messagebox.showerror("", "Select a project first."); return
        self.activity_queue.append(name)
        self._queue_save()
        if self.backup_running:
            self.queue_running = True
        self._queue_refresh()
        self._log_r(f"Queued: {self._project_label(name)}", "a")

    def _queue_waiting_index_from_selection(self):
        if not hasattr(self, "queue_list"):
            return None
        sel = self.queue_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if self.current_project_name:
            idx -= 1
        if idx < 0 or idx >= len(self.activity_queue):
            return None
        return idx

    def _queue_move(self, direction):
        idx = self._queue_waiting_index_from_selection()
        if idx is None:
            messagebox.showinfo("", "Select a waiting queue item to move."); return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.activity_queue):
            return
        self.activity_queue[idx], self.activity_queue[new_idx] = self.activity_queue[new_idx], self.activity_queue[idx]
        self._queue_save()
        self._queue_refresh()
        select_idx = new_idx + (1 if self.current_project_name else 0)
        self.queue_list.select_set(select_idx)

    def _queue_remove_selected(self):
        idx = self._queue_waiting_index_from_selection()
        if idx is None:
            messagebox.showinfo("", "Select a waiting queue item to remove."); return
        removed = self.activity_queue.pop(idx)
        self._queue_save()
        self._queue_refresh()
        self._log_r(f"Removed from queue: {self._project_label(removed)}", "d")

    def _queue_skip_current(self):
        if not self.backup_running or not self.current_project_name:
            messagebox.showinfo("", "No project is currently running."); return
        self.skip_current_for_queue = True
        self.queue_running = bool(self.activity_queue)
        self.stop_event.set()
        self._queue_refresh()
        self._log_r(f"Skipping current: {self._project_label(self.current_project_name)}", "y")

    def _queue_requeue_current(self):
        if not self.backup_running or not self.current_project_name:
            messagebox.showinfo("", "No project is currently running."); return
        self.activity_queue.append(self.current_project_name)
        self._queue_save()
        self.skip_current_for_queue = True
        self.queue_running = True
        self.stop_event.set()
        self._queue_refresh()
        self._log_r(f"Moved current to end: {self._project_label(self.current_project_name)}", "y")

    def _queue_clear(self):
        if self.backup_running and self.queue_running:
            if not messagebox.askyesno("Clear queue", "Clear waiting items? The current task will keep running."):
                return
        self.activity_queue.clear()
        self._queue_save()
        self.queue_running = False
        self._queue_refresh()
        self._log_r("Queue cleared.", "d")

    def _queue_start(self):
        if self.backup_running:
            if self.activity_queue:
                self.queue_running = True
                self._queue_refresh()
                self._log_r("Queue will continue after the current task.", "a")
            else:
                messagebox.showinfo("", "Add waiting projects to the queue first.")
            return
        if not self.activity_queue:
            messagebox.showinfo("", "Add at least one project to the queue."); return
        self.queue_running = True
        self._queue_run_next()

    def _queue_run_next(self):
        if self.backup_running:
            return
        if not self.queue_running or not self.activity_queue:
            self.queue_running = False
            self._log_r("Queue complete.", "g")
            return
        name = self.activity_queue.pop(0)
        self._queue_save()
        self._queue_refresh()
        self._log_r(f"Queue starting: {self._project_label(name)}", "a")
        self.stop_event.clear()
        self._start(project_name=name, from_queue=True)

    def _start(self, project_name=None, from_queue=False):
        sel=project_name or self._run_selected_project_name()
        if not sel: messagebox.showerror("","Select a project."); return
        p=next((x for x in self.config_data["projects"] if x["name"]==sel),None)
        if not p: return
        ai=self.config_data.get("api_id",""); ah=self.config_data.get("api_hash","")
        if not ai or not ah: messagebox.showerror("","Complete Setup first."); return
        filters=p.get("filters",{"messages":True,"images":True,"files":True,"skip_duplicates":True})
        if self.backup_running:
            if not from_queue:
                messagebox.showwarning("Running", "A task is still running.\nWait for it to stop before starting another.")
            return
        self.stop_event.clear(); self.backup_running=True
        mode=p.get("mode","auto")
        self.active_task = "download" if mode == "download" else "backup"
        self.current_project_name = p.get("name")
        self._queue_refresh()
        self.start_btn.state(["disabled"]); self.stop_btn.state(["!disabled"])
        if hasattr(self, "d_start_btn"): self.d_start_btn.state(["disabled"])
        self._log_r(f"Starting {self._project_kind(p).lower()}: {p['name']}","a")
        if mode=="download":
            self._log_r(f"  {p.get('source_name','?')}  →  {p.get('output_dir') or p.get('dest_name','?')}\n","d")
            self._start_download_project(p, ai, ah, filters)
        elif mode=="auto":
            self._log_r(f"  {p.get('source_name','?')}  →  {p.get('dest_name','?')}\n","d")
            threading.Thread(target=lambda:run_backup_auto(
                int(ai),ah,p["source_id"],p["dest_id"],p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()
        elif mode=="routes":
            routes=p.get("routes",[])
            self._log_r(f"  {len(routes)} route(s) via Project Builder\n","d")
            threading.Thread(target=lambda:run_backup_routes(
                int(ai),ah,routes,p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()
        else:
            self._log_r(f"  {len(p.get('mappings',[]))} mapping(s)\n","d")
            threading.Thread(target=lambda:run_backup_manual(
                int(ai),ah,p["mappings"],p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()

    def _stop(self):
        self.queue_running = False
        self.stop_event.set(); self._log_r("Stop requested. Queue paused.","r")
        if self.active_task == "download":
            self._log_d("Stop requested...", "r")

    def _log_should_follow(self, widget):
        try:
            return widget.yview()[1] >= 0.98
        except Exception:
            return True

    def _log_finish(self, widget, should_follow):
        if should_follow:
            widget.see(tk.END)

    def _set_progress(self, text):
        if not hasattr(self, "prog_lbl"):
            return
        self.prog_lbl.config(text=text)
        if text and not self.prog_lbl.winfo_manager():
            self.prog_lbl.pack(anchor="w", padx=22, pady=(2,0))

    def _log_r(self,msg,tag=""):
        follow = self._log_should_follow(self.run_log)
        self.run_log.insert(tk.END,msg+"\n",tag)
        self._log_finish(self.run_log, follow)

    def _log_d(self, msg, tag=""):
        if hasattr(self, "download_log"):
            follow = self._log_should_follow(self.download_log)
            self.download_log.insert(tk.END, msg + "\n", tag)
            self._log_finish(self.download_log, follow)

    def _log_download_live_status(self, msg):
        if not hasattr(self, "download_log"):
            return
        follow = self._log_should_follow(self.download_log)
        if not hasattr(self, "_download_live_mark"):
            self._download_live_mark = "download_live_status"
        mark = self._download_live_mark
        try:
            self.download_log.index(mark)
            line_start = self.download_log.index(f"{mark} linestart")
            self.download_log.delete(line_start, f"{line_start} lineend+1c")
            self.download_log.mark_set(mark, line_start)
            self.download_log.mark_gravity(mark, tk.LEFT)
            self.download_log.insert(mark, "◉  " + msg + "\n", "status")
        except tk.TclError:
            self.download_log.mark_set(mark, tk.END)
            self.download_log.mark_gravity(mark, tk.LEFT)
            self.download_log.insert(tk.END, "◉  " + msg + "\n", "status")
        if follow:
            self.download_log.see(mark)

    def _clear_download_live_status(self):
        if hasattr(self, "download_log") and hasattr(self, "_download_live_mark"):
            try:
                self.download_log.delete(self._download_live_mark, f"{self._download_live_mark} lineend+1c")
            except tk.TclError:
                pass

    def _log_download_progress(self, msg):
        m = re.match(
            r"Download progress: \[(?P<label>.+?)\] (?P<topic>\d+/\d+ this topic \([^)]+\)) \| "
            r"(?P<skipped>\d+ skipped), (?P<errors>\d+ errors) \| "
            r"(?P<run>\d+/\d+ this download \([^)]+\))$",
            msg
        )
        if not m:
            self._log_d("◉  " + msg, "g")
            return
        skipped_n = int(m.group("skipped").split()[0])
        errors_n = int(m.group("errors").split()[0])
        parts = [
            ("◉  Download: ", "g"),
            ("[", "d"),
            (m.group("label"), "a"),
            ("] ", "d"),
            (m.group("topic"), "status"),
            (" | ", "d"),
            (m.group("skipped"), "y" if skipped_n else "d"),
            (", ", "d"),
            (m.group("errors"), "r" if errors_n else "d"),
            (" | ", "d"),
            (m.group("run"), "copied"),
            ("\n", ""),
        ]
        if hasattr(self, "download_log"):
            follow = self._log_should_follow(self.download_log)
            for text, tag in parts:
                self.download_log.insert(tk.END, text, tag)
            self._log_finish(self.download_log, follow)

    def _log_progress_r(self, msg):
        m = re.match(
            r"Progress: \[(?P<label>.+?)\] (?P<topic>\d+/\d+ this topic \([^)]+\)) \| "
            r"(?P<skipped>\d+ skipped), (?P<errors>\d+ errors) \| "
            r"(?P<run>\d+/\d+ this run \([^)]+\))$",
            msg
        )
        if not m:
            self._log_r("◉  " + msg, "g")
            return
        skipped_n = int(m.group("skipped").split()[0])
        errors_n = int(m.group("errors").split()[0])
        parts = [
            ("◉  Progress: ", "g"),
            ("[", "d"),
            (m.group("label"), "a"),
            ("] ", "d"),
            (m.group("topic"), "status"),
            (" | ", "d"),
            (m.group("skipped"), "y" if skipped_n else "d"),
            (", ", "d"),
            (m.group("errors"), "r" if errors_n else "d"),
            (" | ", "d"),
            (m.group("run"), "copied"),
            ("\n", ""),
        ]
        follow = self._log_should_follow(self.run_log)
        for text, tag in parts:
            self.run_log.insert(tk.END, text, tag)
        self._log_finish(self.run_log, follow)

    # ── Poll ───────────────────────────────────────────────────────────────────
    def _poll(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg == "__DONE__":
                    was_download = self.active_task == "download"
                    should_continue_queue = self.queue_running and (not self.stop_event.is_set() or self.skip_current_for_queue)
                    self.backup_running = False
                    self.active_task = None
                    self.current_project_name = None
                    self.skip_current_for_queue = False
                    self._queue_refresh()
                    if hasattr(self, "start_btn"): self.start_btn.state(["!disabled"])
                    if hasattr(self, "stop_btn"): self.stop_btn.state(["disabled"])
                    if hasattr(self, "d_start_btn"): self.d_start_btn.state(["!disabled"])
                    if hasattr(self, "d_stop_btn"): self.d_stop_btn.state(["disabled"])
                    self._log_r("─" * 50, "d")
                    if was_download:
                        self._clear_download_live_status()
                        self._log_d("─" * 50, "d")
                    if should_continue_queue:
                        self.after(500, self._queue_run_next)
                elif any(x in msg for x in ("ERROR","FATAL","PermissionError","protected")):
                    self._log_r("✗  " + msg, "r"); self._log_s(msg, "err")
                    if self.active_task == "download": self._log_d("✗  " + msg, "r")
                elif msg.startswith("Download progress:"):
                    self._log_download_progress(msg)
                    self._set_progress(msg)
                elif msg.startswith("DOWNLOAD_STATUS "):
                    clean = msg[len("DOWNLOAD_STATUS "):]
                    if hasattr(self, "d_status_lbl"):
                        self.d_status_lbl.config(text=clean, fg=ACCENT)
                    self._log_download_live_status(clean)
                    self._set_progress(clean)
                elif any(x in msg for x in ("Download destination:", "Download pending:", "Download items this run:", "Download size:", "Counting download items")):
                    self._log_d(msg, "d")
                    self._log_r(msg, "d")
                elif msg.startswith("Downloading file:"):
                    self._log_d("⬇  " + msg, "status")
                    self._log_r("⬇  " + msg, "status")
                elif msg.startswith("Downloaded file:") or msg.startswith("Saving text:"):
                    if msg.startswith("Downloaded file:"):
                        self._clear_download_live_status()
                    self._log_d("✓  " + msg, "g")
                    self._log_r("✓  " + msg, "g")
                elif any(x in msg for x in ("Downloading topic:", "Done topic:", "Download complete:")):
                    tag = "g" if "complete" in msg or "Done topic" in msg else "a"
                    self._log_d(("✓  " if tag == "g" else "▶  ") + msg, tag)
                    self._log_r(("✓  " if tag == "g" else "▶  ") + msg, tag)
                elif "Progress:" in msg:
                    self._log_progress_r(msg); self._set_progress(msg)
                elif msg.startswith("STATUS "):
                    clean = msg[7:]  # strip "STATUS "
                    self._log_r("◉  " + clean, "g"); self._set_progress(clean)
                elif "Skipping duplicate" in msg:
                    self._log_r("⊘  " + msg, "y")
                elif "WARNING" in msg:
                    self._log_r("⚠  " + msg, "y")
                elif any(x in msg for x in ("Done.", "complete", "All mappings")):
                    self._log_r("✓  " + msg, "g"); self._set_progress(msg)
                elif any(x in msg for x in ("Flood wait","flood")):
                    self._log_r("⏸  " + msg, "y")
                elif any(x in msg for x in ("Logged in","Connected","✓")):
                    self._log_r("✓  " + msg, "g"); self._log_s(msg, "ok")
                elif any(x in msg for x in ("Stopped","Stop")):
                    self._log_r("■  " + msg, "r")
                elif any(x in msg for x in ("Starting ","──","route","Route")):
                    self._log_r(msg, "a")
                elif any(x in msg for x in ("Topics ready","Syncing","Counting","Resuming","fresh","dest id")):
                    self._log_r(msg, "d")
                elif any(x in msg for x in ("Copying topic","Creating topic")):
                    self._log_r("▶  " + msg, "a")
                elif any(x in msg for x in ("retry","Request error","Connection")):
                    self._log_r("↺  " + msg, "y")
                else:
                    self._log_r(msg); self._log_s(msg)
        except queue.Empty:
            pass
        self.after(300, self._poll)


if __name__=="__main__":
    app=App(); app.mainloop()


