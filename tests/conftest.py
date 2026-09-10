"""
Adds the project root to sys.path so tests can `import digital_dna`,
`import network_os`, etc. directly -- this project is a flat
collection of scripts, not an installable package.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
