// Mirrors src/oqed/events.py. runs/<run_id>.jsonl holds one event per line.
export function parseJsonl(text) {
    return text
        .split("\n")
        .filter((l) => l.trim())
        .map((l) => JSON.parse(l));
}
