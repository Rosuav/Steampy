import asyncio
import base64
import itertools
import json
import traceback
import zlib
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
#sys.path.append("pb_webui")
#import_service("service_steamnotification_pb2")
# TODO: Figure out how to load up two separate namespaces of protobufs. Currently,
# loading anything from pb_webui breaks the main protobuf collection.

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

def pb_to_dict(pb):
	return {f.name: v for f, v in pb.ListFields()}

def protobuf_http(service, method, /, _http_method="POST", _credentials=None, **args):
	srv = services_by_name[service] # Error here probably means we need to import another module of protobufs
	meth = srv.methods_by_name[method] # Error here likely means a bug, wrong method name for this service
	# Using private attribute _concrete_class seems wrong, is there a better way to construct this?
	msg = meth.input_type._concrete_class(**args)
	xtra = { }
	xtra["params" if _http_method == "GET" else "data"] = params = {
		"input_protobuf_encoded": base64.b64encode(msg.SerializeToString()),
	}
	if _credentials: params["access_token"] = _credentials["access_token"]
	resp = requests.request(_http_method, "https://api.steampowered.com/I%sService/%s/v1" % (service, method), **xtra)
	if resp.headers["Content-Type"] != "application/octet-stream":
		print("Bad response")
		print(resp.headers)
		print(resp.content)
		raise Exception() # can't be bothered
	return meth.output_type._concrete_class.FromString(resp.content)

steamsock = None
jobid = itertools.count(2) # Job #1 is the "Hello" message, to avoid collisions
jobs_pending = { }
_have_credentials = False # Hack - change the emsg when we have a steamid
async def websocket_listen(notif=None):
	global steamsock
	endpoint = "ext1-syd1.steamserver.net:27037"
	async with websockets.connect(f"wss://{endpoint}/cmsocket/") as steamsock:
		emsg = 9805 # ClientHello
		import steammessages_clientserver_login_pb2
		msg = steammessages_clientserver_login_pb2.CMsgClientHello(protocol_version=65581)
		hdr = steammessages_base_pb2.CMsgProtoBufHeader(
			jobid_source=1,
		)
		hdr = hdr.SerializeToString()
		data = (emsg | 0x80000000).to_bytes(4, "little") + len(hdr).to_bytes(4, "little") + hdr + msg.SerializeToString()
		await steamsock.send(data)
		if notif: notif.set_result(steamsock)
		while True:
			data = await steamsock.recv()
			parse_response(data)

async def protobuf_ws(service, method, /, **args):
	if steamsock is None:
		f = asyncio.Future()
		spawn(websocket_listen(f))
		await f
	srv = services_by_name[service] # Error here probably means we need to import another module of protobufs
	meth = srv.methods_by_name[method] # Error here likely means a bug, wrong method name for this service
	# Using private attribute _concrete_class seems wrong, is there a better way to construct this?
	msg = meth.input_type._concrete_class(**args)
	emsg = 151 if _have_credentials else 9804
	job = next(jobid)
	fut = asyncio.Future()
	jobs_pending[job] = (fut, meth.output_type._concrete_class)
	hdr = steammessages_base_pb2.CMsgProtoBufHeader(
		target_job_name=service + "." + method + "#1",
		jobid_source=job,
	)
	hdr = hdr.SerializeToString()
	data = (emsg | 0x80000000).to_bytes(4, "little") + len(hdr).to_bytes(4, "little") + hdr + msg.SerializeToString()
	await steamsock.send(data)
	return await fut

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

def parse_response(data):
	emsg = int.from_bytes(data[:4], "little")
	if emsg & 0x80000000:
		# It's protobuf
		emsg &= 0x7fffffff
		if emsg == 1:
			# EMsgMulti
			#print("Multi", len(data) - 8)
			# No idea what the next four bytes mean - they seem to be zero.
			# They'd be the header length if this were a single-message packet.
			#print("Next four bytes", int.from_bytes(data[4:8], "little"))
			multi = steammessages_base_pb2.CMsgMulti.FromString(data[8:])
			msg = multi.message_body
			if multi.size_unzipped:
				#print("HAS COMPRESSED DATA", multi.size_unzipped)
				# NOTE: The compressed stream has a gzip header/trailer. Python's zlib module
				# can parse this, but needs to be told. I'm not sure whether the window size
				# matters here (I'm using the maximum possible of 15, plus 16 to signal that
				# it's a gzip header instead of zlib), so if weird decompression failures
				# begin happening, try adjusting this second parameter.
				decomp = zlib.decompressobj(31)
				msg = decomp.decompress(msg)
				if not decomp.eof:
					print("Decompression not complete!!")
					# TODO: How to handle this?? Probably fail hard.
				#print("DECOMPRESSED:", len(msg))
				#print("Residue:", decomp.unused_data) # Should always be empty, I think?
			while msg:
				size = int.from_bytes(msg[:4], "little")
				raw = msg[4:4+size]
				msg = msg[size+4:]
				parse_response(raw)
		elif emsg == 2:
			# EMsgProtobufWrapped - not sure if we see this
			msg = steammessages_base_pb2.CMsgProtobufWrapped.FromString(data[4:]).message_body
			print("PBWrapped", msg)
		elif emsg == 147:
			# EMsgServiceMethodResponse
			print("Response", len(data))
			hdrlen = int.from_bytes(data[4:8], "little")
			ret = hdr = steammessages_base_pb2.CMsgProtoBufHeader.FromString(data[8:8+hdrlen])
			fut, cls = jobs_pending.pop(hdr.jobid_target, (None, None))
			print(hdr)
			print(pb_to_dict(hdr))
			if cls: ret = cls.FromString(data[8+hdrlen:])
			if fut: fut.set_result(ret)
		else:
			print("Unknown emsg", emsg, data)
			return
	else:
		# Non-protobuf messages, not currently parsed
		print("Non-protobuf", emsg, data[4:])

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

	user = "sanctified_toaster"
	password = "not-my-real-password"
	import getpass; password = getpass.getpass()
	pk = requests.post("https://steamcommunity.com/login/getrsakey", {"username": user}).json()
	key = rsa.PublicKey(int(pk["publickey_mod"], 16),
		int(pk["publickey_exp"], 16))
	password = password.encode("ascii") # Encoding error? See if Steam uses UTF-8.
	password = base64.b64encode(rsa.encrypt(password, key))
	login = await protobuf_ws("Authentication", "BeginAuthSessionViaCredentials",
		device_friendly_name="SteamAuthPy",
		account_name=user,
		website_id="Mobile",
		platform_type=1, # Steam client
		#guard_data=details.GuardData, # Retain this to allow passwordless relogin
		encrypted_password=password,
		encryption_timestamp=int(pk["timestamp"]),
		#device_details = new CAuthentication_DeviceDetails
		remember_login=True,
		persistence=1,
		device_details={"platform_type": 1, "app_type": 1},
		# optional .CAuthentication_DeviceDetails device_details = 9;
		# optional uint32 language = 11;
		# optional int32 qos_level = 12 [default = 2];
	)
	print("LOGIN", login.allowed_confirmations)
	twofer = getpass.getpass("2FA: ")
	global _have_credentials; _have_credentials = True
	print(await protobuf_ws("Authentication", "UpdateAuthSessionWithSteamGuardCode",
		#client_id=login.client_id,
		steamid=login.steamid,
		code=twofer,
		code_type=3,
	))
	sess = await protobuf_ws("Authentication", "PollAuthSessionStatus",
		#client_id=login.client_id,
		#request_id=login.request_id,
	)
	data = {f.name: v for f, v in sess.ListFields()}
	print(data)
	#with open("SECRET.json", "w") as f: json.dump(data, f)
	print(list(data))
	print("----")
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
	#await steamsock.send(data)

async def get_time():
	reply = await protobuf_ws("TwoFactor", "QueryTime")
	print("Time", reply)

async def notifs():
	with open("SECRET.json") as f: creds = json.load(f)
	prefs = protobuf_http("SteamNotification", "GetSteamNotifications", _http_method="GET", _credentials=creds,
		include_hidden=True,
		include_pinned_counts=True,
	)
	print(prefs)

async def main():
	await login()
	# await notifs()
	# await get_time()

asyncio.run(main())
