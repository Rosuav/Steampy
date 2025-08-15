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

steamsock = None
jobid = itertools.count(0xdeaded) # An arbitrary bluebottle so that job IDs are recognizable in dumps
jobs_pending = { }
_have_credentials = False # Hack - change the emsg when we have a steamid

async def send_protobuf_msg(emsg, hdr, msg, output_type):
	job = next(jobid)
	if output_type:
		fut = asyncio.Future()
		jobs_pending[job] = (fut, output_type)
	hdr.jobid_source = job
	hdr = hdr.SerializeToString()
	data = (emsg | 0x80000000).to_bytes(4, "little") + len(hdr).to_bytes(4, "little") + hdr + msg.SerializeToString()
	await steamsock.send(data)
	if output_type:
		return await fut

async def websocket_listen(notif=None):
	global steamsock
	endpoint = "ext1-syd1.steamserver.net:27037"
	async with websockets.connect(f"wss://{endpoint}/cmsocket/") as steamsock:
		import steammessages_clientserver_login_pb2
		await send_protobuf_msg(
			9805, # ClientHello
			steammessages_base_pb2.CMsgProtoBufHeader(),
			steammessages_clientserver_login_pb2.CMsgClientHello(protocol_version=65581),
			None)
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
	return await send_protobuf_msg(
		151 if _have_credentials else 9804,
		steammessages_base_pb2.CMsgProtoBufHeader(
			service + "." + method + "#1",
		),
		meth.input_type._concrete_class(**args),
		meth.output_type._concrete_class)

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
		elif emsg == 751:
			global _have_credentials; _have_credentials = True
			print("Logged on successfully!", len(data))
		else:
			print("Unknown PB emsg", emsg, data)
			return
	else:
		# Non-protobuf messages
		if emsg == 113:
			# Job failed. No idea what info we get, but the job ID seems to be found three bytes into
			# the packet body (7 bytes in, counting the emsg four bytes).
			jobid = int.from_bytes(data[7:11], "little")
			fut, cls = jobs_pending.pop(jobid, (None, None))
			if fut: fut.set_exception(Exception()) # TODO: Better exception
		else:
			print("Unknown non-pb emsg", emsg, data[4:])
			print("Pending:", jobs_pending)

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
	# Email code is 2, device code (TOTP) is 3
	# None is 1, so it might be that we'd see that rather than an empty list?
	# An empty list seems to happen when the password is wrong.
	confirm = [c.confirmation_type for c in login.allowed_confirmations]
	print("LOGIN", confirm)
	print("Poll", await protobuf_ws("Authentication", "PollAuthSessionStatus",
		client_id=login.client_id,
		request_id=login.request_id,
	))
	if 3 in confirm:
		print("Device code needed - may be able to automate this")
	twofer = getpass.getpass("2FA: ")
	sendcode = await protobuf_ws("Authentication", "UpdateAuthSessionWithSteamGuardCode",
		client_id=login.client_id,
		steamid=login.steamid,
		code=twofer,
		code_type=confirm[0] if confirm else 4,
	)
	print("Send code", sendcode)
	sess = await protobuf_ws("Authentication", "PollAuthSessionStatus",
		client_id=login.client_id,
		request_id=login.request_id,
	)
	data = {f.name: v for f, v in sess.ListFields()}
	if "refresh_token" not in data:
		print("Login failed, available keys:", list(data))
		return
	with open("SECRET.json", "w") as f: json.dump(data, f)

async def get_time():
	reply = await protobuf_ws("TwoFactor", "QueryTime")
	print("Time", reply)

async def notifs():
	with open("SECRET.json") as f: creds = json.load(f)
	import steammessages_clientserver_login_pb2
	f = asyncio.Future()
	spawn(websocket_listen(f))
	await f
	# Grab the Steam ID from the JWT
	steamid = json.loads(base64.b64decode(creds["refresh_token"].split(".")[1]))["sub"]
	resp = await send_protobuf_msg(
		5514, # ClientLogOn
		steammessages_base_pb2.CMsgProtoBufHeader(
			steamid=int(steamid)
		),
		steammessages_clientserver_login_pb2.CMsgClientLogon(
			# obfuscated_private_ip
			account_name=creds["account_name"],
			should_remember_password=True,
			protocol_version=65581,
			access_token=creds["refresh_token"],
	# optional uint32 protocol_version = 1;
	# optional uint32 deprecated_obfustucated_private_ip = 2;
	# optional uint32 cell_id = 3;
	# optional uint32 last_session_id = 4;
	# optional uint32 client_package_version = 5;
	# optional string client_language = 6;
	# optional uint32 client_os_type = 7;
	# optional bool should_remember_password = 8 [default = false];
	# optional string wine_version = 9;
	# optional uint32 deprecated_10 = 10;
	# optional .CMsgIPAddress obfuscated_private_ip = 11;
	# optional uint32 deprecated_public_ip = 20;
	# optional uint32 qos_level = 21;
	# optional fixed64 client_supplied_steam_id = 22;
	# optional .CMsgIPAddress public_ip = 23;
	# optional bytes machine_id = 30;
	# optional uint32 launcher_type = 31 [default = 0];
	# optional uint32 ui_mode = 32 [default = 0];
	# optional uint32 chat_mode = 33 [default = 0];
	# optional bytes steam2_auth_ticket = 41;
	# optional string email_address = 42;
	# optional fixed32 rtime32_account_creation = 43;
	# optional string account_name = 50;
	# optional string password = 51;
	# optional string game_server_token = 52;
	# optional string login_key = 60;
	# optional bool was_converted_deprecated_msg = 70 [default = false];
	# optional string anon_user_target_account_name = 80;
	# optional fixed64 resolved_user_steam_id = 81;
	# optional int32 eresult_sentryfile = 82;
	# optional bytes sha_sentryfile = 83;
	# optional string auth_code = 84;
	# optional int32 otp_type = 85;
	# optional uint32 otp_value = 86;
	# optional string otp_identifier = 87;
	# optional bool steam2_ticket_request = 88;
	# optional bytes sony_psn_ticket = 90;
	# optional string sony_psn_service_id = 91;
	# optional bool create_new_psn_linked_account_if_needed = 92 [default = false];
	# optional string sony_psn_name = 93;
	# optional int32 game_server_app_id = 94;
	# optional bool steamguard_dont_remember_computer = 95;
	# optional string machine_name = 96;
	# optional string machine_name_userchosen = 97;
	# optional string country_override = 98;
	# optional bool is_steam_box = 99;
	# optional uint64 client_instance_id = 100;
	# optional string two_factor_code = 101;
	# optional bool supports_rate_limit_response = 102;
	# optional string web_logon_nonce = 103;
	# optional int32 priority_reason = 104;
	# optional .CMsgClientSecret embedded_client_secret = 105;
	# optional bool disable_partner_autogrants = 106;
	# optional bool is_steam_deck = 107;
	# optional string access_token = 108;
	# optional bool is_chrome_os = 109;
	# optional bool is_tesla = 110;
		),
		steammessages_clientserver_login_pb2.CMsgClientLogonResponse,
	)
	print("Got response", resp)
	# prefs = protobuf_http("SteamNotification", "GetSteamNotifications", _http_method="GET", _credentials=creds,
		# include_hidden=True,
		# include_pinned_counts=True,
	# )
	# print(prefs)

async def main():
	# await login()
	await notifs()
	# await get_time()

asyncio.run(main())
