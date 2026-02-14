
from docopt import docopt

doc = """Usage:
  test_docopt.py --service=<name>...
"""

if __name__ == '__main__':
    args = docopt(doc, argv=['--service=s1', '--service=s2'])
    print(f"args['--service'] type: {type(args['--service'])}")
    print(f"args['--service'] value: {args['--service']}")
