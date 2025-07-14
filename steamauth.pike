void onclose(mixed ... args) {
	werror("Closed, exiting - %O\n", args);
	exit(0);
}

void onmessage(mixed ... args) {
	werror("Message received: %O\n", args);
}

void onopen(object conn) {
	werror("Opened %O\n", conn);
	string msg = MIME.decode_base64("TCYAgEMAAABRewAAAAAAAABZewAAAAAAAABiL0F1dGhlbnRpY2F0aW9uLkJlZ2luQXV0aFNlc3Npb25WaWFDcmVkZW50aWFscyMxEgZSb3N1YXYa2AJoUHZrRUZUOS82ZllyMkI4UHBlOGF0dkVXSmFxaGRTNnV4WndTeWJlbUZDMGZpY3U3OVV5enRrUTh0MGswdDE1N3grRUdsSUJwSDN1OEgzamhIQzY5K3pwbnRHbHhoTXhqSDl2Y0tvTWVmWUEvWXUyMXpaYjd4d0ZzZno0cHRodTBNaVlKUEd6UkJ1MThHVSt1enZidFVGTjdSUzZGYnNEN0J5U3F5eElZZVNBS1RTTjlZSDV4M1oyTis1WTg2MERKYWRXNWYwVis5WXFPY2tmT1BjRGg0RE93YzVxb296aldwWjdjTlozSXlRREFOQTRjb0ZlRFQwczlQcWo4VWRqSkIrdThiUDNqNTIydEZKaTZsVUwrNVpORjZrN2RWT0R4a2FtNm84OGRYWnZCNUxwajN1cnh1MFdtVlNUSkxCbVpHZmJlSlI0M2h4QVcvYkgrc1VHOEE9PQ==");
	conn->send_binary(msg);
}

int main() {
	object conn = Protocols.WebSocket.Connection();
	conn->onclose = onclose;
	conn->onmessage = onmessage;
	conn->onopen = onopen;
	conn->connect("wss://ext1-syd1.steamserver.net:27037/cmsocket/");
	return -1;
}
