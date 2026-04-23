import sys
import os
import grpc
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONFILE', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/books_database'))

import database_pb2
import database_pb2_grpc

logger = logging.getLogger(__name__)

# N=3 replicas, W=2, R=2  →  W+R > N  (overlap guaranteed)
QUORUM_SIZE = 2


def quorum_read(key, local_store, peer_addresses):
    """
    Quorum read: contact self + all peers in parallel, wait for QUORUM_SIZE
    responses, return the entry with the highest version number.

    Args:
        key            (str):  the key to look up
        local_store    (KVStore): this node's local store
        peer_addresses (list[str]): peer host:port strings, e.g. ["database2:50055"]

    Returns:
        (value, version, found)  where value/version come from the most up-to-date replica
    """
    results = []  # list of (value, version, found)

    # ── 1. Read locally (counts as one response) ──────────────────────────
    local_value, local_version = local_store.local_read(key)
    results.append((local_value or "", local_version, local_value is not None))
    logger.info(f"[quorum_read] local  key={key} version={local_version}")

    if len(results) >= QUORUM_SIZE:
        # Already have quorum from local alone (only possible if QUORUM_SIZE==1,
        # kept here for safety — won't fire with QUORUM_SIZE=2)
        best = max(results, key=lambda r: r[1])
        return best

    # ── 2. Read from peers in parallel ────────────────────────────────────
    def read_from_peer(addr):
        with grpc.insecure_channel(addr) as channel:
            stub = database_pb2_grpc.BooksDatabaseStub(channel)
            resp = stub.LocalRead(database_pb2.ReadRequest(key=key), timeout=3)
            logger.info(f"[quorum_read] peer={addr} key={key} version={resp.version}")
            return resp.value, resp.version, resp.found

    with ThreadPoolExecutor(max_workers=len(peer_addresses)) as executor:
        futures = {executor.submit(read_from_peer, addr): addr for addr in peer_addresses}
        for future in as_completed(futures):
            addr = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning(f"[quorum_read] peer={addr} failed: {exc}")

            if len(results) >= QUORUM_SIZE:
                # Cancel remaining futures — we have enough
                for f in futures:
                    f.cancel()
                break

    if len(results) < QUORUM_SIZE:
        logger.error(f"[quorum_read] key={key} — only {len(results)} response(s), quorum not reached")
        raise RuntimeError(f"Read quorum not reached for key '{key}': only {len(results)}/{QUORUM_SIZE} responses")

    # ── 3. Return the response with the highest version ───────────────────
    best = max(results, key=lambda r: r[1])
    logger.info(f"[quorum_read] key={key} → version={best[1]} found={best[2]}")
    return best  # (value, version, found)


def quorum_write(key, value, expected_version, local_store, peer_addresses):
    """
    Quorum write: write to self + all peers in parallel, wait for QUORUM_SIZE
    successful ACKs to confirm the write.

    The caller is responsible for passing the correct expected_version (obtained
    via a prior quorum_read). Each replica uses optimistic concurrency control:
    a write is only accepted if the stored version matches expected_version.

    Args:
        key              (str):    the key to write
        value            (str):    the new value
        expected_version (int):    the version the caller read — must match each replica's current version
        local_store      (KVStore): this node's local store
        peer_addresses   (list[str]): peer host:port strings

    Returns:
        (success: bool, new_version: int, error: str)
    """
    acks = []   # list of new_version ints from successful writes
    errors = [] # failure reasons

    # ── 1. Write locally ──────────────────────────────────────────────────
    success, new_version = local_store.local_write(key, value, expected_version)
    if success:
        acks.append(new_version)
        logger.info(f"[quorum_write] local  key={key} new_version={new_version}")
    else:
        errors.append(f"local: version mismatch (expected {expected_version}, got {new_version})")
        logger.warning(f"[quorum_write] local write rejected: {errors[-1]}")

    # ── 2. Write to peers in parallel ─────────────────────────────────────
    def write_to_peer(addr):
        with grpc.insecure_channel(addr) as channel:
            stub = database_pb2_grpc.BooksDatabaseStub(channel)
            resp = stub.LocalWrite(
                database_pb2.WriteRequest(
                    key=key,
                    value=value,
                    expected_version=expected_version,
                ),
                timeout=3,
            )
            logger.info(f"[quorum_write] peer={addr} key={key} success={resp.success} version={resp.version}")
            return resp.success, resp.version, resp.error

    with ThreadPoolExecutor(max_workers=len(peer_addresses)) as executor:
        futures = {executor.submit(write_to_peer, addr): addr for addr in peer_addresses}
        for future in as_completed(futures):
            addr = futures[future]
            try:
                ok, ver, err = future.result()
                if ok:
                    acks.append(ver)
                else:
                    errors.append(f"{addr}: {err}")
                    logger.warning(f"[quorum_write] peer={addr} rejected write: {err}")
            except Exception as exc:
                errors.append(f"{addr}: {exc}")
                logger.warning(f"[quorum_write] peer={addr} failed: {exc}")

            if len(acks) >= QUORUM_SIZE:
                for f in futures:
                    f.cancel()
                break

    # ── 3. Check quorum ───────────────────────────────────────────────────
    if len(acks) >= QUORUM_SIZE:
        agreed_version = max(acks)  # all successful writes return the same version
        logger.info(f"[quorum_write] key={key} → quorum reached, new_version={agreed_version}")
        return True, agreed_version, ""

    error_summary = "; ".join(errors)
    logger.error(f"[quorum_write] key={key} — only {len(acks)}/{QUORUM_SIZE} ACKs: {error_summary}")
    return False, expected_version, f"Write quorum not reached: {error_summary}"