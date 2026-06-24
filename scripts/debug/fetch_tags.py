# -*- coding: utf-8 -*-
"""Fetch product tags from Master API for the charging bank project."""
import json
import socket
import sys


def raw_get(path, workspace_id="de90965d-29e4-4ac3-9730-0ce1fc85b67c"):
    sock = socket.create_connection(("127.0.0.1", 8000), timeout=10)
    raw = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:8000\r\n"
        f"X-Workspace-Id: {workspace_id}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    sock.sendall(raw.encode("utf-8"))
    resp = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp += chunk
    sock.close()
    hdr_end = resp.find(b"\r\n\r\n")
    body = resp[hdr_end + 4 :]
    if b"chunked" in resp[:hdr_end].lower():
        decoded = b""
        idx = 0
        while idx < len(body):
            le = body.find(b"\r\n", idx)
            if le == -1:
                break
            sz = int(body[idx:le], 16)
            if sz == 0:
                break
            decoded += body[le + 2 : le + 2 + sz]
            idx = le + 2 + sz + 2
        body = decoded
    return json.loads(body.decode("utf-8"))


def main():
    scheme_id = "901abfd6-8fb7-4110-ab23-3fee96f7bf5a"
    data = raw_get(f"/api/schemes/{scheme_id}/summary")
    scheme = data.get("scheme", data)
    items = scheme.get("items", [])
    print(f"Items: {len(items)}")
    for item in items:
        uid = item.get("uid", "")
        title = item.get("title", "") or item.get("name", "")
        tags = item.get("tags", "")
        label = item.get("tag", "") or item.get("label", "")
        print(f"  {uid:12s} {title:30s} tags={tags}  label={label}")
    if items:
        print("\nFirst item keys:", list(items[0].keys()))
        print("First item:", json.dumps(items[0], ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
