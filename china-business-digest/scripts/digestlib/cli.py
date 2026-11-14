import argparse
import json
import smtplib
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .collect import collect_sources
from .config import load_configuration
from .emailer import SMTPConfig, send_email
from .errors import DigestError
from .http import Fetcher
from .state import State, exclusive_file_lock
from .summarize import create_digest
from .summarize_client import OpenAICompatibleClient
from .util import atomic_write_text, utc_now


def collection_notes(result, sources):
    names = {source.id: source.name for source in sources}
    notes = [f"{names.get(source_id, source_id)}: {message}" for source_id, message in result.errors.items()]
    warnings = list(result.warnings)
    notes.extend(warnings[:4])
    if len(warnings) > 4:
        notes.append(f"{len(warnings) - 4} additional detail-fetch warnings were recorded locally.")
    return [" ".join(note.split())[:240] for note in notes]


def save_collection(build_dir, result):
    destination = build_dir / "latest_candidates.json"
    atomic_write_text(destination, json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return destination


def collect(state, args, sources, keywords, build_dir):
    now = utc_now()
    if args.lookback_hours is not None:
        lookback = args.lookback_hours
    else:
        lookback = 72 if state.has_collection_run() else args.initial_lookback_hours
    cutoff = now - timedelta(hours=lookback)
    result = collect_sources(
        sources, keywords, state, Fetcher(state), cutoff, max_items=args.max_items, now=now,
    )
    if result.succeeded:
        state.mark_collection()
    path = save_collection(build_dir, result)
    return result, path


def save_preview(build_dir, run_date, digest, rendered):
    directory = build_dir / "digests"
    base = directory / run_date
    atomic_write_text(base.with_suffix(".json"), json.dumps(digest, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(base.with_suffix(".txt"), rendered.text)
    atomic_write_text(base.with_suffix(".html"), rendered.html)
    return base


def print_collection(result, candidate_path):
    print(f"Collected {len(result.items)} candidate(s) from {len(result.succeeded)} source(s).")
    for item in result.items:
        date = item.published_at.date().isoformat() if item.published_at else "no date"
        print(f"- [{item.source_id}] {date} {item.title}")
    for source_id, message in result.errors.items():
        print(f"WARNING {source_id}: {message}")
    for warning in result.warnings:
        print(f"WARNING detail: {warning}")
    print(f"Candidates: {candidate_path}")


def deliver_smtp(config, subject, text_body, html_body):
    try:
        send_email(config, subject, text_body, html_body)
    except (smtplib.SMTPException, OSError) as error:
        raise DigestError(
            "SMTP delivery failed or had an uncertain result. Check the mailbox and saved preview; "
            "use send --force only after deciding whether a resend is needed."
        ) from error


def run_command(args, root):
    build_dir = root / "build"
    sources, keywords = load_configuration(root / "data" / "sources.json")
    run_date = args.date or datetime.now().astimezone().date().isoformat()
    if args.date:
        try:
            run_date = datetime.strptime(args.date, "%Y-%m-%d").date().isoformat()
        except ValueError as error:
            raise DigestError("--date must use YYYY-MM-DD") from error
    with exclusive_file_lock(build_dir / "digest.lock"):
        with State(build_dir / "state.sqlite3") as state:
            if args.command == "send":
                existing = state.get_run(run_date)
                if existing and not args.force:
                    if existing["status"] == "sent":
                        print(f"A digest was already sent for {run_date}; no duplicate was sent.")
                        return
                    raise DigestError(
                        f"An interrupted or uncertain delivery exists for {run_date}. "
                        "Check the mailbox and saved preview, then use send --force to resend deliberately."
                    )
                if existing and args.force:
                    config = SMTPConfig.from_environment()
                    deliver_smtp(config, existing["subject"], existing["text_body"], existing["html_body"])
                    state.mark_sent(run_date)
                    print("The saved digest was deliberately resent.")
                    return

            result, candidate_path = collect(state, args, sources, keywords, build_dir)
            print_collection(result, candidate_path)
            if not result.succeeded:
                raise DigestError("All configured sources failed; no digest was generated or sent")
            if args.command == "collect":
                return

            notes = collection_notes(result, sources)
            client = OpenAICompatibleClient(state) if result.items else None
            digest, rendered = create_digest(result.items, run_date, notes, client)
            base = save_preview(build_dir, run_date, digest, rendered)
            print(f"Preview: {base.with_suffix('.txt')}")
            print(f"HTML: {base.with_suffix('.html')}")
            print(f"English body length: {rendered.word_count} words (limit 600; Chinese translation excluded).")
            if args.command == "preview":
                return

            config = SMTPConfig.from_environment()
            state.record_pending(
                run_date, rendered.subject, digest, rendered.text, rendered.html,
                [item.key for item in result.items],
            )
            deliver_smtp(config, rendered.subject, rendered.text, rendered.html)
            state.mark_sent(run_date)
            print("SMTP accepted the digest; the daily run was recorded.")


def build_parser():
    parser = argparse.ArgumentParser(description="Collect, summarise, preview, and email a China business digest.")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("collect", "preview", "send"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--date", help="Run date in YYYY-MM-DD (for preview/testing)")
        subparser.add_argument("--lookback-hours", type=int, default=None)
        subparser.add_argument("--initial-lookback-hours", type=int, default=168)
        subparser.add_argument("--max-items", type=int, default=12)
        if command == "send":
            subparser.add_argument("--force", action="store_true", help="Resend the saved digest for this date")
    return parser


def main(argv=None, root=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lookback_hours is not None and not 1 <= args.lookback_hours <= 24 * 31:
        parser.error("--lookback-hours must be between 1 hour and 31 days")
    if not 1 <= args.initial_lookback_hours <= 24 * 31:
        parser.error("--initial-lookback-hours must be between 1 hour and 31 days")
    if not 1 <= args.max_items <= 20:
        parser.error("--max-items must be between 1 and 20")
    root = Path(root) if root else Path(__file__).resolve().parent.parent.parent
    try:
        run_command(args, root)
    except DigestError as error:
        parser.exit(1, f"error: {error}\n")
