import asyncio
import json
import os
import re
import sys


def _is_writable_dir(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def _copy_config_if_needed(src_folder, dest_folder):
    src = os.path.join(src_folder, "tg_backup_config.json")
    dest = os.path.join(dest_folder, "tg_backup_config.json")
    if not os.path.exists(src) or os.path.exists(dest):
        return
    try:
        with open(src, encoding="utf-8") as f:
            data = f.read()
        with open(dest, "w", encoding="utf-8") as f:
            f.write(data)
    except Exception:
        pass


def _use_existing_app_data():
    if not getattr(sys, "frozen", False):
        return
    exe_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(exe_dir, "App Files", "App data"),
        exe_dir,
    ]
    for folder in candidates:
        if os.path.exists(os.path.join(folder, "tg_backup_config.json")) and _is_writable_dir(folder):
            os.chdir(folder)
            return
    default_folder = os.path.join(exe_dir, "App Files", "App data")
    if _is_writable_dir(default_folder):
        os.chdir(default_folder)
        return
    fallback = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Telegram Group Backup Download")
    if _is_writable_dir(fallback):
        for folder in candidates:
            _copy_config_if_needed(folder, fallback)
        os.chdir(fallback)
        return


_use_existing_app_data()

CONFIG_FILE = "tg_backup_config.json"
PREVIEW_IMAGE_LIMIT = 3
PREVIEW_IMAGE_WINDOW_SECONDS = 120


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"api_id": "", "api_hash": "", "phone": "", "projects": []}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_json_file(path, data, **dump_kwargs):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump_kwargs)
    except PermissionError:
        try:
            os.chmod(path, 0o666)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, **dump_kwargs)
        except Exception:
            raise


def new_loop(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def is_generic_topic_title(title, topic_id):
    title = (title or "").strip()
    return not title or title == f"Topic {topic_id}"


def is_protected_content_error(err):
    text = str(err).lower()
    return (
        "protected chat" in text
        or "protected content" in text
        or "can't forward messages" in text
        or "cannot forward messages" in text
    )


def is_invalid_media_error(err):
    text = str(err).lower()
    return "answer_0_media_invalid" in text or "media_invalid" in text


def is_no_retry_send_error(err):
    return is_protected_content_error(err) or is_invalid_media_error(err)


def send_error_label(err):
    if is_protected_content_error(err):
        return "protected chat content"
    if is_invalid_media_error(err):
        return "invalid media"
    return str(err)


async def safe_req(fn, stop_event, log_q, retries=5):
    from telethon.errors import FloodWaitError

    attempt = 0
    while True:
        if stop_event.is_set():
            raise InterruptedError("Stopped")
        try:
            return await fn()
        except FloodWaitError as e:
            log_q.put(f"Flood wait: {e.seconds}s (~{e.seconds//60} min)...")
            for _ in range(e.seconds + 5):
                if stop_event.is_set():
                    raise InterruptedError("Stopped")
                await asyncio.sleep(1)
            attempt = 0
        except InterruptedError:
            raise
        except Exception as e:
            if is_no_retry_send_error(e):
                raise
            attempt += 1
            wait = min(60, 15 * attempt)
            log_q.put(f"Request error ({e}), retry {attempt}/{retries} in {wait}s...")
            if attempt >= retries:
                raise
            await asyncio.sleep(wait)


async def fetch_all_topics(client, src_entity, log_q):
    from telethon.tl.functions.messages import GetForumTopicsRequest

    topics = {}
    seen = set()
    offset_topic = 0
    offset_id = 0
    offset_date = None
    page = 1
    total = None
    while True:
        log_q.put(f"Fetching topic page {page}...")
        result = await client(GetForumTopicsRequest(
            peer=src_entity,
            offset_date=offset_date,
            offset_id=offset_id,
            offset_topic=offset_topic,
            limit=100,
        ))
        if total is None and hasattr(result, "count"):
            total = result.count
            log_q.put(f"Source has {total} topics total")
        log_q.put(f"Got {len(result.topics)} topics (fetched so far: {len(seen)})")
        if not result.topics:
            break
        new_count = 0
        for topic in result.topics:
            if topic.id not in seen:
                seen.add(topic.id)
                topics[topic.id] = topic.title
                new_count += 1
        if new_count == 0:
            break
        if total and len(seen) >= total:
            log_q.put(f"All {total} topics fetched")
            break
        last = result.topics[-1]
        offset_topic = last.id
        offset_id = getattr(last, "top_message", 0) or 0
        offset_date = getattr(last, "date", None)
        page += 1
    return topics


def message_link_urls(msg):
    text = msg.message or ""
    urls = []

    def add_url(url):
        if not url:
            return
        url = str(url).strip()
        if not url or url in urls or url in text:
            return
        urls.append(url)

    for ent in getattr(msg, "entities", None) or []:
        ent_url = getattr(ent, "url", None)
        if ent_url:
            add_url(ent_url)
            continue
        if ent.__class__.__name__ == "MessageEntityUrl":
            try:
                add_url(text[ent.offset:ent.offset + ent.length])
            except Exception:
                pass
    for row in getattr(msg, "buttons", None) or []:
        buttons = row if isinstance(row, (list, tuple)) else [row]
        for button in buttons:
            add_url(getattr(button, "url", None))
    reply_markup = getattr(msg, "reply_markup", None)
    for row in getattr(reply_markup, "rows", []) or []:
        for button in getattr(row, "buttons", []) or []:
            add_url(getattr(button, "url", None))
    webpage = getattr(getattr(msg, "media", None), "webpage", None)
    add_url(getattr(webpage, "url", None))
    add_url(getattr(webpage, "display_url", None))
    return urls


def message_text_with_links(msg):
    text = msg.message or ""
    urls = message_link_urls(msg)
    if not urls:
        return text
    link_block = "\n".join(urls)
    if text.strip():
        return text.rstrip() + "\n\nLinks:\n" + link_block
    return "Links:\n" + link_block


def message_needs_link_repair(msg):
    return bool(message_link_urls(msg)) and message_text_with_links(msg) != (msg.message or "")


def media_kind(media):
    if not media:
        return "text"
    name = media.__class__.__name__
    if name == "MessageMediaPhoto":
        return "photo"
    if name == "MessageMediaDocument":
        return "document"
    return name


def get_fn(media):
    try:
        for attr in media.document.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                return attr.file_name
    except Exception:
        pass
    return None


def get_sz(media):
    try:
        return media.document.size
    except Exception:
        return None


def is_dup(sent_files, tid, fn, sz):
    if not fn or not sz:
        return False
    return sent_files.get(str(tid), {}).get(fn) == sz


def track(sent_files, tid, fn, sz):
    if not fn or not sz:
        return
    key = str(tid)
    if key not in sent_files:
        sent_files[key] = {}
    sent_files[key][fn] = sz


def safe_name(name, fallback="item"):
    cleaned = re.sub(r'[<>:"/\\|-*\x00-\x1f]', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or fallback


def fmt_bytes(n):
    n = int(n or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{n} B"
        size /= 1024


def doc_mime(media):
    try:
        return (media.document.mime_type or "").lower()
    except Exception:
        return ""


def is_gif_media(media):
    mime = doc_mime(media)
    if mime == "image/gif":
        return True
    try:
        from telethon.tl.types import DocumentAttributeAnimated

        return any(isinstance(attr, DocumentAttributeAnimated) for attr in media.document.attributes)
    except Exception:
        return False


def is_sticker_media(media):
    mime = doc_mime(media)
    if mime in ("application/x-tgsticker", "image/webp"):
        return True
    try:
        from telethon.tl.types import DocumentAttributeSticker

        return any(isinstance(attr, DocumentAttributeSticker) for attr in media.document.attributes)
    except Exception:
        return False


def is_poll_media(media):
    if not media:
        return False
    if media.__class__.__name__ == "MessageMediaPoll":
        return True
    return bool(getattr(media, "poll", None))


def is_image_document(media):
    mime = doc_mime(media)
    return bool(getattr(media, "document", None)) and mime.startswith("image/") and not is_gif_media(media) and not is_sticker_media(media)


def is_video_media(media):
    mime = doc_mime(media)
    if not mime.startswith("video/") or is_gif_media(media):
        return False
    try:
        from telethon.tl.types import DocumentAttributeVideo

        return any(isinstance(attr, DocumentAttributeVideo) for attr in media.document.attributes)
    except Exception:
        return mime.startswith("video/")


def is_standalone_decoration_media(media):
    return is_gif_media(media) or is_sticker_media(media) or is_poll_media(media) or is_video_media(media)


def is_download_file(media):
    mime = doc_mime(media)
    if not media or is_gif_media(media) or is_video_media(media):
        return False
    return bool(getattr(media, "document", None)) and not mime.startswith("image/")


def photo_size(media):
    try:
        sizes = getattr(media.photo, "sizes", []) or []
        return max((getattr(size, "size", 0) or 0) for size in sizes) if sizes else 0
    except Exception:
        return 0


def download_media_size(media):
    try:
        return media.document.size
    except Exception:
        return photo_size(media)


def get_src_thread(msg, known_topic_ids=None):
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
