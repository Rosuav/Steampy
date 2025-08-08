import asyncio
import traceback
import base64
import requests
import websockets
import rsa # ImportError? 'pip install rsa' or equivalent.

# Protobuf structures are all in a git-ignored directory
import sys, importlib
sys.path.append("protobuf")
import steammessages_base_pb2 # Might not need this eventually
services_by_name = { }
def import_service(modname):
	mod = importlib.import_module(modname)
	services_by_name.update(mod.DESCRIPTOR.services_by_name)
import_service("steammessages_auth.steamclient_pb2")
import_service("steammessages_twofactor.steamclient_pb2")

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

def protobuf_http(service, method, /, **args):
	srv = services_by_name[service] # Error here probably means we need to import another module of protobufs
	meth = srv.methods_by_name[method] # Error here likely means a bug, wrong method name for this service
	# Using private attribute _concrete_class seems wrong, is there a better way to construct this?
	msg = meth.input_type._concrete_class(**args)
	resp = requests.post("https://api.steampowered.com/I%sService/%s/v1" % (service, method), data={
		"input_protobuf_encoded": base64.b64encode(msg.SerializeToString()),
	})
	return meth.output_type._concrete_class.FromString(resp.content)

def timecheck():
	reply = protobuf_http("TwoFactor", "QueryTime")
	import time
	print(reply)
	print(reply.server_time - time.time())

def make_qr():
	print(protobuf_http("Authentication", "BeginAuthSessionViaQR"))
	# Hypothetically, the QR code auth flow would start with the above, then generate a QR code
	# from reply.challenge_url. The user points the Steam app at the challenge URL QR code. We
	# poll (every reply.interval seconds) using PollAuthSessionStatus until we get a reply.

async def recv(conn):
	while True:
		data = await conn.recv()
		print("RECEIVED", data)

# To test a message's decoding:
# msg = "CLmdtLLJvf7t5QESEBRD03J1KBMlnaPyKh6VmBg="
# msg = base64.b64decode(msg)
# print(steammessages_auth.steamclient_pb2.CAuthentication_PollAuthSessionStatus_Request.FromString(msg))
# Seems to be fine; the inner layers of message don't seem to be an issue. It's the wrapping around them that
# we're having issues with - the CMsgProtoBufHeader perhaps.

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
		spawn(recv(conn))

		# Before trying to log in, can we just get time synchronization?
		# msg = message_types["TwoFactor"].CTwoFactor_Time_Request()
		# emsg = 9804 # or use 151 once we're authed
		# hdr = steammessages_base_pb2.CMsgProtoBufHeader(
			# target_job_name="TwoFactor.QueryTime#1",
			# jobid_source=1,
		# )
		# hdr = hdr.SerializeToString()
		# data = (emsg | 0x80000000).to_bytes(4, "little") + len(hdr).to_bytes(4, "little") + hdr + msg.SerializeToString()
		# await conn.send(data)

		user = "Rosuav"
		password = "not-my-real-password"
		# import getpass; password = getpass.getpass()
		pk = requests.post("https://steamcommunity.com/login/getrsakey", {"username": user}).json()
		key = rsa.PublicKey(int(pk["publickey_mod"], 16),
			int(pk["publickey_exp"], 16))
		password = password.encode("ascii") # Encoding error? See if Steam uses UTF-8.
		password = base64.b64encode(rsa.encrypt(password, key))
		reply = protobuf_http("Authentication", "BeginAuthSessionViaCredentials",
			device_friendly_name="SteamAuthPy",
			account_name=user,
			website_id="Mobile",
			platform_type=1, # Steam client
			#guard_data=details.GuardData, # Retain this to allow passwordless relogin
			encrypted_password=password,
			encryption_timestamp=int(pk["timestamp"]),
			#device_details = new CAuthentication_DeviceDetails
			# optional bool remember_login = 5;
			# optional .ESessionPersistence persistence = 7 [default = k_ESessionPersistence_Persistent];
			# optional .CAuthentication_DeviceDetails device_details = 9;
			# optional uint32 language = 11;
			# optional int32 qos_level = 12 [default = 2];
		)
		print(reply)
		emsg = 9804 # or use 151 once we're authed
		hdr = steammessages_base_pb2.CMsgProtoBufHeader(
			#messageid=emsg, # Does this need to be here as well?
			# Do we need to set the source job ID?
			target_job_name="Authentication.BeginAuthSessionViaCredentials#1",
			jobid_source=123,
			jobid_target=123,
		)
		hdr = hdr.SerializeToString()
		# data = (emsg | 0x80000000).to_bytes(4, "little") + len(hdr).to_bytes(4, "little") + hdr + msg.SerializeToString()
		#print(base64.b64encode(data))
		#import hashlib; print(hashlib.sha256(data).hexdigest())
		#await conn.send(data)
		print("Sent...")
		await asyncio.sleep(3)
		print("Ending.")

async def main():
	await login()

asyncio.run(main())
