"""
local_verify_node.py
================
Starts one real NetworkNode on 127.0.0.1:18765 and idles, for the
verify.ps1 / verify.bat / verify.sh scripts (and
.github/workflows/verify.yml) to point an interop client at during a
local or CI verification run. Not part of the actual project runtime
-- run_all.py is the real entry point; this exists purely so the
three shell-specific verify scripts don't each need their own copy of
this snippet with different quoting rules.

Run directly: python scripts/local_verify_node.py
Stop with Ctrl+C, or kill the process -- state (dna_state file) is
written to local_verify_node.dna.json in the current working
directory and isn't needed between runs (verify scripts delete it
after).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from digital_dna import DigitalDNA
from network_os import NetworkNode


async def main():
    dna = DigitalDNA(seed_label="local-verify-node", dna_path="local_verify_node.dna.json")
    node = NetworkNode(dna, host="127.0.0.1", port=18765)
    await node.start()
    print(f"node_id={node.node_id}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
