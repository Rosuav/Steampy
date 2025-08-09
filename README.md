$ git clone https://github.com/steamdatabase/protobufs.git ../valveprotobufs
$ protoc --python_out=protobuf --proto_path=../valveprotobufs/steam ../valveprotobufs/steam/*.proto
$ protoc --python_out=protobuf --proto_path=../valveprotobufs/webui ../valveprotobufs/webui/*.proto

Note that the webui collection from steamdb has some collisions that mean that two files
fail to compile: webui/service_steamvr{voicechat,webrtc}.proto
The easiest fix is to delete or rename those before running protoc.
