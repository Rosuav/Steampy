import asyncio
import traceback
import base64
import requests
import websockets
import rsa # ImportError? 'pip install rsa' or equivalent.

# Protobuf structures are all in a git-ignored directory
import sys
sys.path.append("protobuf")
import steammessages_auth.steamclient_pb2

def handle_errors(task):
	try:
		exc = task.exception() # Also marks that the exception has been handled
		if exc: traceback.print_exception(type(exc), exc, exc.__traceback__)
	except asyncio.exceptions.CancelledError:
		pass

all_tasks = [] # kinda like threading.all_threads()

def task_done(task):
	all_tasks.remove(task)
	handle_errors(task)

def spawn(awaitable):
	"""Spawn an awaitable as a stand-alone task"""
	task = asyncio.create_task(awaitable)
	all_tasks.append(task)
	task.add_done_callback(task_done)
	return task

async def recv(conn):
	while True:
		data = await conn.recv()
		print("RECEIVED", data)

async def login():
	# 1. requests.get("https://api.steampowered.com/ISteamDirectory/GetCMListForConnect/v1/").json()
	# and pick out an entry with type "websockets"
	# or just hard-code endpoint = "ext1-syd1.steamserver.net:27037"
	# 2. Establish a websocket connection to wss://{endpoint}/cmsocket/
	# 3. steamClient.Authentication.BeginAuthSessionViaCredentialsAsync
	# 3a. May need TOTP
	# 4. authSession.PollingWaitForResultAsync();
	# 4a. Save the Guard Data which should be a JWT that will allow us to bypass login in the future
	# 5. steamUser.LogOn( new SteamUser.LogOnDetails)
	endpoint = "ext1-syd1.steamserver.net:27037"
	async with websockets.connect(f"wss://{endpoint}/cmsocket/") as conn:
		print(conn)
		spawn(recv(conn))
		user = "Rosuav"
		password = "not-my-real-password"
		data = requests.post("https://steamcommunity.com/login/getrsakey", {"username": user}).json()
		key = rsa.PublicKey(int(data["publickey_mod"], 16),
			int(data["publickey_exp"], 16))
		password = password.encode("ascii") # Encoding error? See if Steam uses UTF-8.
		password = base64.b64encode(rsa.encrypt(password, key))
		print(password)
		await asyncio.sleep(3)

async def main():
	await login()

asyncio.run(main())
