"""Run only on the separate database-connector host, not inside the agent."""
import argparse
from pathlib import Path
import re

from mcp_server import MCP, Server
from policy import Config, Policy, SafeError, strict_json
from upstream import InfluxClient, read_secret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        with Path(args.config).open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise SafeError("Configuration too large.")
        config = Config(strict_json(raw))
        username, password, token = [read_secret(p) for p in config.secret_paths]
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise SafeError("Invalid MCP token.")
        client = InfluxClient(config, username, password)
        policy = Policy(config, client, (username, password, token, client.authorization))
        server = Server((config.listen_host, config.listen_port), MCP(policy), token, config.hosts)
    except Exception:
        raise SystemExit("Startup refused; check configuration and service secret permissions locally.") from None
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
