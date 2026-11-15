"""Command-line entry point: ``python -m nisar_tool``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .errors import ApiError
from .server import build_bundle, create_server, search_parameters


def _write_json(value, output=None):
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


def _write_binary(content, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _search_arguments(parser):
    parser.add_argument("--bbox", required=True, help="west,south,east,north (WGS84)")
    parser.add_argument("--product", default="GCOV", help="NISAR product type (default: GCOV)")
    parser.add_argument("--start", help="Acquisition start ISO date or timestamp")
    parser.add_argument("--end", help="Acquisition end ISO date or timestamp")
    parser.add_argument("--limit", type=int, default=5, help="Maximum products, 1–20")
    parser.add_argument("--previews", choices=("none", "links", "inline"), default="links")


def main(argv=None):
    parser = argparse.ArgumentParser(description="NISAR discovery, provider previews and reviewable subset plans. Preview images are not scientific radar rasters.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the loopback-only HTTP API and helper interface")
    serve.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4174)
    serve.add_argument("--public-origin", help="One exact HTTPS tunnel origin to trust (or set NISAR_PUBLIC_ORIGIN)")
    search = commands.add_parser("search", help="Search NISAR products and return JSON")
    _search_arguments(search)
    search.add_argument("--output", help="Write JSON to a file instead of stdout")
    preview = commands.add_parser("preview", help="Save one provider preview image")
    preview.add_argument("product_id")
    preview.add_argument("--index", type=int, default=0)
    preview.add_argument("--output", required=True)
    bundle = commands.add_parser("bundle", help="Save metadata plus separate provider preview images as ZIP")
    _search_arguments(bundle)
    bundle.add_argument("--output", required=True)
    plan = commands.add_parser("subset-plan", help="Create a Harmony subset handoff; does not submit a processing job")
    plan.add_argument("--product", required=True)
    plan.add_argument("--collection", required=True, help="CMR collection concept ID")
    plan.add_argument("--granule", required=True, help="CMR granule concept ID")
    plan.add_argument("--bbox", required=True, help="west,south,east,north (WGS84)")
    plan.add_argument("--variables", nargs="+", help="Optional product variable names")
    plan.add_argument("--output", help="Write the reviewable handoff file; otherwise print the plan JSON")
    # argparse otherwise mistakes a comma-separated western-hemisphere bbox for
    # an option when callers use the natural ``--bbox -77,34,-76,35`` spelling.
    arguments = list(sys.argv[1:] if argv is None else argv)
    normalized = []
    position = 0
    while position < len(arguments):
        value = arguments[position]
        if value == "--bbox" and position + 1 < len(arguments) and arguments[position + 1].startswith("-") and arguments[position + 1].count(",") == 3:
            normalized.append("--bbox=" + arguments[position + 1])
            position += 2
        else:
            normalized.append(value)
            position += 1
    args = parser.parse_args(normalized)
    try:
        if args.command == "subset-plan":
            from .harmony import build_subset_plan, write_handoff
            bbox = search_parameters({"bbox": args.bbox})["bbox"]
            try:
                value = build_subset_plan(args.product.upper(), args.collection, args.granule, bbox, variables=args.variables)
            except ValueError as error:
                raise ApiError("invalid_subset_plan", str(error)) from None
            if args.output:
                path = write_handoff(value, args.output)
                _write_json({"handoff": str(path), "submitted": False})
            else:
                _write_json(value)
            return 0

        from .runtime import create_client
        client = create_client()
        if args.command == "serve":
            server = create_server(client, host=args.host, port=args.port, public_origin=args.public_origin)
            print(f"NISAR local API: http://{args.host}:{server.server_port}/", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        if args.command in {"search", "bundle"}:
            params = search_parameters({key: getattr(args, key) for key in ("bbox", "start", "end", "product", "limit", "previews")})
            if args.command == "search":
                _write_json(client.search(**params), args.output)
            else:
                content = build_bundle(client, params)
                path = _write_binary(content, args.output)
                _write_json({"output": str(path), "size_bytes": len(content), "scientific": False})
            return 0
        if args.index < 0:
            raise ApiError("invalid_preview_index", "The preview index must be zero or greater.")
        result = client.preview(args.product_id, index=args.index)
        path = _write_binary(result["content"], args.output)
        _write_json({key: value for key, value in result.items() if key != "content"} | {"output": str(path), "scientific": False})
        return 0
    except ApiError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message, "status": error.status, "details": error.details}}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"error": {"code": "local_error", "message": "The command could not complete. Check the output path and local configuration.", "status": 500, "details": None}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
