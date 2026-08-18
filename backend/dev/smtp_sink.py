"""
backend/dev/smtp_sink.py — DEV-ONLY local SMTP capture server.

Runs a real SMTP server on 127.0.0.1:1025 (no auth, no TLS) that ACCEPTS
messages and saves them as .eml files under dev/smtp_capture/ plus echoes a
one-line summary per message. This lets the CUS grievance system exercise the
full outbound-email path (SMTP connect -> envelope -> DATA -> 250 accepted)
on a machine that has no internet SMTP credentials.

It is NOT a mail-delivery service; it exists so email delivery can be
verified end-to-end locally. Replace SMTP_HOST/PORT/USER/PASSWORD in
backend/.env with a real provider (Gmail, Outlook, Zoho, SMTP2GO, ...) and
stop this sink when real inbox delivery is required.

Usage:
    python dev/smtp_sink.py [port]        (default port 1025)
"""

from __future__ import annotations

import socketserver
import sys
import time
from email.parser import BytesParser
from pathlib import Path

HOST = "127.0.0.1"
SPOOL = Path(__file__).resolve().parent / "smtp_capture"


class SmtpHandler(socketserver.StreamRequestHandler):
    timeout = 60

    def sendline(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("utf-8", "replace"))

    def recvline(self) -> str:
        raw = self.rfile.readline()
        if not raw:
            return ""
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    def handle(self) -> None:  # noqa: D102
        self.sendline(f"220 {HOST} CUS dev smtp sink ready")
        envelope_from = ""
        recipients: list[str] = []
        while True:
            line = self.recvline()
            if not line:
                return
            cmd, _, rest = line.partition(" ")
            verb = cmd.upper()
            if verb == "HELO":
                self.sendline(f"250 {HOST}")
            elif verb == "EHLO":
                self.sendline(f"250-{HOST}")
                self.sendline("250 OK")
            elif verb == "MAIL":
                envelope_from = rest.removeprefix("FROM:").strip()
                self.sendline("250 OK")
            elif verb == "RCPT":
                recipients.append(rest.removeprefix("TO:").strip())
                self.sendline("250 OK")
            elif verb == "DATA":
                self.sendline("354 End data with <CR><LF>.<CR><LF>")
                data = bytearray()
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b".\r\n", b".\n"):
                        break
                    if chunk.startswith(b"."):
                        chunk = chunk[1:]
                    data += chunk
                self._store(envelope_from, recipients, bytes(data))
                self.sendline("250 OK: queued as dev-sink")
            elif verb == "RSET":
                envelope_from = ""
                recipients = []
                self.sendline("250 OK")
            elif verb == "QUIT":
                self.sendline("221 Bye")
                return
            else:
                self.sendline("250 OK")

    def _store(self, envelope_from: str, recipients: list[str], raw: bytes) -> None:
        SPOOL.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = SPOOL / f"{stamp}-{len(list(SPOOL.glob('*.eml'))):03d}.eml"
        path.write_bytes(raw)
        try:
            msg = BytesParser().parsebytes(raw)
            subject = (msg.get("Subject") or "?").strip().replace("\n", " ")
            to = (msg.get("To") or ",".join(recipients)).strip()
            lines = ["=== SMTP SINK ACCEPTED ===",
                     f"from: {envelope_from or msg.get('From', '?')}",
                     f"to:   {to}",
                     f"subject: {subject}",
                     f"saved: {path}"]
            print("\n".join(lines), flush=True)
        except Exception:  # noqa: BLE001 — sink never crashes on bad input
            print(f"=== SMTP SINK ACCEPTED (raw) to={recipients} saved={path}",
                  flush=True)


class ThreadedSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1025
    with ThreadedSmtp((HOST, port), SmtpHandler) as server:
        print(f"CUS dev SMTP sink listening on {HOST}:{port} "
              f"(spool: {SPOOL})", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()