$ git clone https://github.com/steamdatabase/protobufs.git ../valveprotobufs
$ protoc --python_out=protobuf --proto_path=../valveprotobufs/steam ../valveprotobufs/steam/*.proto
$ protoc --python_out=pb_webui --proto_path=../valveprotobufs/webui ../valveprotobufs/webui/*.proto

Note that the webui collection from steamdb has some collisions that mean that two files
fail to compile: webui/service_steamvr{voicechat,webrtc}.proto
The easiest fix is to delete or rename those before running protoc.


Login cookies
-------------

Currently I have no way to get the Steam web UI login cookie, which is a requirement for
confirming trades and other notifications. The easiest way is to log in using a normal
web browser, lift the cookie, and save it into SECRET.json in the appropriate user's info
block. I've no idea how long the cookie is good for, or if it retains its viability when
used in the browser itself, but this works when there's one essential requirement.
