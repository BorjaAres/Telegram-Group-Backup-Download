from tg_shared import is_generic_topic_title, load_config, new_loop


_phone_code_hash = {}


def do_send_code(api_id, api_hash, phone, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            log_q.put("ERROR: pip install telethon")
            result_q.put(False)
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                log_q.put(f"Already logged in as {me.first_name} (@{me.username})")
                result_q.put(True)
                return
            sent = await client.send_code_request(phone)
            _phone_code_hash["hash"] = sent.phone_code_hash
            log_q.put("Code sent to your Telegram app.")
            result_q.put("need_code")
        except Exception as e:
            log_q.put(f"Login error: {e}")
            result_q.put(False)
        finally:
            await client.disconnect()

    new_loop(_r())


def do_signin(api_id, api_hash, phone, code, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            result_q.put(False)
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            await client.sign_in(phone=phone, code=code, phone_code_hash=_phone_code_hash.get("hash", ""))
            me = await client.get_me()
            log_q.put(f"Logged in as {me.first_name} (@{me.username})")
            result_q.put(True)
        except Exception as e:
            log_q.put(f"Sign in error: {e}")
            result_q.put(False)
        finally:
            await client.disconnect()

    new_loop(_r())


def list_groups(api_id, api_hash, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
        except ImportError:
            result_q.put([])
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log_q.put("Not logged in.")
                result_q.put(None)
                return
            groups = []
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    groups.append({"name": dialog.name, "id": dialog.id})
            log_q.put(f"Found {len(groups)} groups/channels.")
            result_q.put(groups)
        except Exception as e:
            log_q.put(f"Error: {e}")
            result_q.put([])
        finally:
            await client.disconnect()

    new_loop(_r())


def list_topics(api_id, api_hash, group_id, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetForumTopicsRequest
        except ImportError:
            result_q.put([])
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            entity = await client.get_entity(group_id)
            if not getattr(entity, "forum", False):
                result_q.put([{"id": 1, "title": "General (no topics)"}])
                return
            topics = []
            seen = set()
            offset_topic = 0
            offset_id = 0
            offset_date = None
            total = None
            while True:
                result = await client(GetForumTopicsRequest(
                    peer=entity,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=100,
                ))
                if total is None and hasattr(result, "count"):
                    total = result.count
                new_found = 0
                for topic in result.topics:
                    if topic.id not in seen:
                        seen.add(topic.id)
                        topics.append({"id": topic.id, "title": topic.title})
                        new_found += 1
                if new_found == 0 or not result.topics:
                    break
                if total and len(seen) >= total:
                    break
                last = result.topics[-1]
                offset_topic = last.id
                offset_id = getattr(last, "top_message", 0) or 0
                offset_date = getattr(last, "date", None)
            topic_by_id = {int(topic["id"]): topic for topic in topics}
            try:
                cfg = load_config()
                known = {}
                for project in cfg.get("projects", []):
                    for route in project.get("routes", []):
                        if int(route.get("src_group_id", 0) or 0) == int(group_id):
                            known[int(route.get("src_topic_id", 1))] = route.get("src_topic_title") or f"Topic {route.get('src_topic_id', 1)}"
                        if int(route.get("dest_group_id", 0) or 0) == int(group_id) and route.get("dest_topic_id"):
                            known[int(route.get("dest_topic_id"))] = route.get("dest_topic_title") or f"Topic {route.get('dest_topic_id')}"
                for topic_id, saved_title in known.items():
                    if topic_id == 1:
                        continue
                    current = topic_by_id.get(topic_id, {"id": topic_id, "title": saved_title})
                    if topic_id not in topic_by_id or is_generic_topic_title(current.get("title"), topic_id):
                        try:
                            msg = await client.get_messages(group_id, ids=topic_id)
                            action = getattr(msg, "action", None) if msg else None
                            real_title = getattr(action, "title", None)
                            if real_title:
                                current["title"] = real_title
                        except Exception:
                            pass
                    topic_by_id[topic_id] = current
                topics = sorted(topic_by_id.values(), key=lambda t: (t.get("title", "").lower(), int(t.get("id", 0))))
            except Exception:
                pass
            result_q.put(topics)
        except Exception as e:
            log_q.put(f"Could not load topics: {e}")
            result_q.put(None)
        finally:
            await client.disconnect()

    new_loop(_r())


def resolve_route_topic_names(api_id, api_hash, routes, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.types import MessageActionTopicCreate
        except ImportError:
            result_q.put({"kind": "route_topic_names", "updates": []})
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            updates = []
            cache = {}

            async def title_for(group_id, topic_id):
                key = (int(group_id), int(topic_id))
                if key in cache:
                    return cache[key]
                title = None
                try:
                    msg = await client.get_messages(group_id, ids=topic_id)
                    action = getattr(msg, "action", None) if msg else None
                    title = getattr(action, "title", None)
                    if not title and isinstance(action, MessageActionTopicCreate):
                        title = getattr(action, "title", None)
                except Exception:
                    title = None
                cache[key] = title
                return title

            for idx, route in enumerate(routes):
                src_title = route.get("src_topic_title", "")
                dst_title = route.get("dest_topic_title", "")
                update = {"idx": idx}
                if is_generic_topic_title(src_title, route.get("src_topic_id", 1)):
                    title = await title_for(route["src_group_id"], route.get("src_topic_id", 1))
                    if title:
                        update["src_topic_title"] = title
                if route.get("dest_topic_id") and is_generic_topic_title(dst_title, route.get("dest_topic_id")):
                    title = await title_for(route["dest_group_id"], route.get("dest_topic_id"))
                    if title:
                        update["dest_topic_title"] = title
                if len(update) > 1:
                    updates.append(update)
            result_q.put({"kind": "route_topic_names", "updates": updates})
        except Exception as e:
            log_q.put(f"Could not resolve topic names: {e}")
            result_q.put({"kind": "route_topic_names", "updates": []})
        finally:
            await client.disconnect()

    new_loop(_r())


def create_group(api_id, api_hash, title, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.channels import CreateChannelRequest, ToggleForumRequest
        except ImportError:
            result_q.put(None)
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            res = await client(CreateChannelRequest(title=title, about="", megagroup=True))
            channel = res.chats[0]
            log_q.put(f"Group created: {channel.title}")
            await client(ToggleForumRequest(channel=channel, enabled=True, tabs=False))
            log_q.put("Topics enabled!")
            result_q.put({"name": channel.title, "id": -int(f"100{channel.id}")})
        except Exception as e:
            log_q.put(f"Error: {e}")
            result_q.put(None)
        finally:
            await client.disconnect()

    new_loop(_r())


def create_topic_in_group(api_id, api_hash, group_id, title, log_q, result_q):
    async def _r():
        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import CreateForumTopicRequest
        except ImportError:
            result_q.put(None)
            return
        client = TelegramClient("session_gui", api_id, api_hash)
        try:
            await client.connect()
            dst = await client.get_input_entity(group_id)
            new = await client(CreateForumTopicRequest(peer=dst, title=title))
            new_id = new.updates[0].id
            log_q.put(f"Topic '{title}' created (id {new_id})")
            result_q.put({"id": new_id, "title": title})
        except Exception as e:
            log_q.put(f"Error: {e}")
            result_q.put(None)
        finally:
            await client.disconnect()

    new_loop(_r())
