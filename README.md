$ git clone https://github.com/steamdatabase/protobufs.git ../valveprotobufs
$ protoc --python_out=protobuf --proto_path=../valveprotobufs/steam ../valveprotobufs/steam/*.proto
