import os
import sys

# Tests import the talkse modules directly (they use flat imports like `import db`)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
