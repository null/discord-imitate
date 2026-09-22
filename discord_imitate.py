from json import JSONDecodeError, load, loads, dumps
from requests import post, get, delete, patch, put
from urllib.parse import quote
from websocket import WebSocket
from threading import Thread
from os import system, name
from time import sleep

class DiscordWebsocket:
    def __init__(self) -> None:
        system('cls' if name == 'nt' else 'clear')
        
        with open("config.json") as cfg:
            self.config: dict = load(cfg)

        self.websocket: WebSocket = WebSocket()
        self.heartbeat_interval: float = 41.25
        self.last_sequence: int = None
        self.connection_id: int = 0
        self.message_map: dict = {}

    def heartbeat(self, connection_id: int) -> None:
        while True:
            if connection_id != self.connection_id:
                print("[-] heartbeat | new connection")
                return

            try:
                payload: dict = {
                    "op": 1,
                    "d": self.last_sequence
                }
                self.websocket.send(dumps(payload))
                print("[+] heartbeat")

            except Exception:
                print("[!] heartbeat | lost connection")
                return

            sleep(self.heartbeat_interval)

    def websocket_login(self) -> None:
        try:
            self.websocket.connect("wss://gateway.discord.gg/?encoding=json&v=9")

            hello = loads(self.websocket.recv())
            if hello.get("op") == 10:
                self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

            payload: dict = {
                "op": 2,
                "d": {
                    "token": self.config["token"],
                    "capabilities": 1021,
                    "properties": {
                        "os": "Windows",
                        "browser": "Chrome",
                        "device": "",
                        "system_locale": "en-US",
                        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                        "browser_version": "134.0.0.0",
                        "os_version": "10",
                        "referrer": "",
                        "referring_domain": "",
                        "referrer_current": "",
                        "referring_domain_current": "",
                        "release_channel": "stable",
                        "client_build_number": 380730,
                        "client_event_source": None
                    },
                    "presence": {
                        "status": "unknown",
                        "since": 0,
                        "activities": [],
                        "afk": False
                    },
                    "compress": False,
                    "client_state": {
                        "guild_hashes": {},
                        "highest_last_message_id": "0",
                        "read_state_version": 0,
                        "user_guild_settings_version": -1,
                        "user_settings_version": -1,
                        "private_channels_version": "0"
                    }
                }
            }

            self.websocket.send(dumps(payload))
            print(f"[+] websocket_login | Logged in: {loads(self.websocket.recv())['d']['user']['username']}")

        except Exception as error:
            print(f"[!] websocket_login | {error}")

    def build_embeds(self, data: dict) -> list:
        embeds: list = []
        for data_embed in data.get("embeds", []):
            embed: dict = {}

            for key in ["title", "description", "url", "color", "timestamp", "fields", "type"]:
                if data_embed.get(key):
                    embed[key] = data_embed[key]

            for key in ["footer", "thumbnail", "image", "author", "video"]:
                if data_embed.get(key):
                    embed[key] = {k: v for k, v in data_embed[key].items() if k in ["text", "name", "url", "icon_url", "height", "width"]}

            embeds.append(embed)

        return embeds

    def create_message(self, data: dict) -> None:
        payload: dict = {
            "content": data.get("content", ""),
            "allowed_mentions": {
                "parse": ["users", "roles", "everyone"]
            },
        }

        if data.get("embeds"):
            payload["embeds"] = self.build_embeds(data)

        attachments: list = data.get("attachments", [])

        if not payload["content"] and not payload.get("embeds") and not attachments:
            print(f"[!] create_message | skipped {data.get('id')} - no content/embeds/attachments to send (likely a sticker-only message)")
            return

        if attachments:
            files: dict = {}
            for i, attachment in enumerate(attachments):
                file_bytes = get(attachment["url"]).content
                files[f"files[{i}]"] = (attachment["filename"], file_bytes)

            response = post(
                f"https://discord.com/api/v9/channels/{data['channel_id']}/messages",
                headers={"Authorization": self.config["token"]},
                data={"payload_json": dumps(payload)},
                files=files
            )

        else:
            response = post(
                f"https://discord.com/api/v9/channels/{data['channel_id']}/messages",
                headers={"Authorization": self.config["token"]},
                json=payload
            )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 1)
            print(f"[!] Rate limited, retry in {retry_after}s")
            sleep(retry_after)
            self.create_message(data)

        elif response.ok:
            sent_message: dict = response.json()
            self.message_map[data["id"]] = {
                "channel_id": data["channel_id"],
                "sent_id": sent_message["id"]
            }
            print(f"[+] create_message | {data['id']} > {sent_message['id']}")

        else:
            print(f"[!] create_message failed | {response.status_code} | {response.text}")

    def delete_message(self, data: dict) -> None:
        original_id: str = data.get("id")
        mapping: dict = self.message_map.pop(original_id, None)

        if not mapping:
            return

        response = delete(
            f"https://discord.com/api/v9/channels/{mapping['channel_id']}/messages/{mapping['sent_id']}",
            headers={"Authorization": self.config["token"]}
        )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 1)
            print(f"[!] Rate limited, retry in {retry_after}s")
            sleep(retry_after)
            self.message_map[original_id] = mapping
            self.delete_message(data)

        elif response.status_code == 404:
            print(f"[!] delete_message | {original_id} does not exist")

        elif not response.ok:
            print(f"[!] delete_message failed | {response.status_code} | {response.text}")

        else:
            print(f"[+] delete_message | deleted {original_id}")

    def edit_message(self, data: dict) -> None:
        original_id: str = data.get("id")
        mapping: dict = self.message_map.get(original_id)

        if not mapping:
            return

        if "content" not in data and "embeds" not in data:
            return

        payload: dict = {
            "allowed_mentions": {
                "parse": ["users", "roles", "everyone"]
            },
        }

        if "content" in data:
            payload["content"] = data["content"]

        if data.get("embeds"):
            payload["embeds"] = self.build_embeds(data)

        response = patch(
            f"https://discord.com/api/v9/channels/{mapping['channel_id']}/messages/{mapping['sent_id']}",
            headers={"Authorization": self.config["token"]}, json=payload
        )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 1)
            print(f"[!] Rate limited, retry in {retry_after}s")
            sleep(retry_after)
            self.edit_message(data)

        elif response.status_code == 404:
            print(f"[!] edit_message | echo for {original_id} does not exist")
            self.message_map.pop(original_id, None)

        elif not response.ok:
            print(f"[!] edit_message failed | {response.status_code} | {response.text}")

        else:
            print(f"[+] edit_message | edited {original_id}")

    def format_emoji(self, emoji: dict) -> str:
        if emoji.get("id"):
            return f"{emoji['name']}:{emoji['id']}"
        return quote(emoji["name"])

    def add_reaction(self, data: dict) -> None:
        response = put(
            f"https://discord.com/api/v9/channels/{data['channel_id']}/messages/{data['message_id']}/reactions/{self.format_emoji(data["emoji"])}/@me",
            headers={"Authorization": self.config["token"]}
        )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 1)
            print(f"[!] Rate limited, retry in {retry_after}s")
            sleep(retry_after)
            self.add_reaction(data)

        elif response.status_code == 404:
            print(f"[!] add_reaction | message {data['message_id']} does not exist")

        elif not response.ok:
            print(f"[!] add_reaction failed | {response.status_code} | {response.text}")

        else:
            print(f"[+] add_reaction | added {data['emoji'].get('name')} to {data['message_id']}")

    def remove_reaction(self, data: dict) -> None:
        response = delete(
            f"https://discord.com/api/v9/channels/{data['channel_id']}/messages/{data['message_id']}/reactions/{self.format_emoji(data["emoji"])}/@me",
            headers={"Authorization": self.config["token"]}
        )

        if response.status_code == 429:
            retry_after = response.json().get("retry_after", 1)
            print(f"[!] Rate limited, retry in {retry_after}s")
            sleep(retry_after)
            self.remove_reaction(data)

        elif response.status_code == 404:
            print(f"[!] remove_reaction | {data['message_id']} does not exist")

        elif not response.ok:
            print(f"[!] remove_reaction failed | {response.status_code} | {response.text}")

        else:
            print(f"[+] remove_reaction | removed {data['emoji'].get('name')} from {data['message_id']}")

    def run(self) -> None:
        while True:
            try:
                self.websocket_login()

                self.connection_id += 1
                Thread(target=self.heartbeat, args=(self.connection_id,), daemon=True).start()

                while True:
                    try:
                        response: dict = loads(self.websocket.recv())

                        if response.get("s"):
                            self.last_sequence = response["s"]

                        if response.get("t") == "MESSAGE_CREATE":
                            data: dict = response["d"]
                            if data.get("author", {}).get("id") == self.config["target_user_id"]:
                                self.create_message(data)

                        elif response.get("t") == "MESSAGE_DELETE":
                            data: dict = response["d"]
                            self.delete_message(data)

                        elif response.get("t") == "MESSAGE_UPDATE":
                            data: dict = response["d"]
                            self.edit_message(data)

                        elif response.get("t") == "MESSAGE_REACTION_ADD":
                            data: dict = response["d"]
                            if data.get("user_id") == self.config["target_user_id"]:
                                self.add_reaction(data)

                        elif response.get("t") == "MESSAGE_REACTION_REMOVE":
                            data: dict = response["d"]
                            if data.get("user_id") == self.config["target_user_id"]:
                                self.remove_reaction(data)

                    except JSONDecodeError:
                        continue

            except Exception as error:
                print("[!] Disconnected, reconnect in 5s")
                sleep(5)

if __name__ == "__main__":
    discord_websocket = DiscordWebsocket()
    discord_websocket.run()