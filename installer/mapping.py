"""Recipient-only bridge extraction. Never import or execute the supplied file.

This tool contains no extracted mapping body. The generated module is private,
not a redistributable release asset. Requires Python 3.9+ (ast.unparse).
"""
import ast
import hashlib
from pathlib import Path
import re
import textwrap

SOURCE_SHA256 = 'd69cc40d4a4b3131f900c7021b26f02525f9f90c20569e50c4188c2a5cda5871'
SOURCE_BYTES = 31411
METHODS = ('SetSwitch', 'AddSwitchEvent', 'VPSwitchMatrixToPRSwitch',
           'VPSwitchFlipperToPRSwitch', 'VPSwitchDedToPRSwitch',
           'getGIStates', 'getLampStates', 'getCoilStates')
GENERATOR_VERSION = 1


class MappingError(ValueError):
    pass


class _Port(ast.NodeTransformer):
    """Mechanical Python 2 integer division/membership and instance dependency port."""
    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Div):
            node.op = ast.FloorDiv()
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'has_key':
            if len(node.args) != 1 or node.keywords:
                raise MappingError('Unexpected membership call')
            return ast.Compare(left=node.args[0], ops=[ast.In()], comparators=[node.func.value])
        return node

    def visit_Name(self, node):
        if node.id == 'pinproc':
            return ast.Attribute(value=ast.Name(id='self', ctx=ast.Load()), attr='pinproc', ctx=node.ctx)
        return node

    def visit_Expr(self, node):
        if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == 'self' and node.value.func.attr == '__checkBridgeOK'):
            return None  # COM lifecycle is owned by the native runtime.
        return self.generic_visit(node)


# New native orchestration only; all numeric mapping bodies come from input.
WRAPPER = '''class CCCWPCMapping(object):
    def __init__(self, game, pinproc, lifecycle):
        if game.machine_type != pinproc.MachineTypeWPC95:
            raise ValueError('This bounded port supports CCC WPC95 only')
        self.game, self.pinproc, self.lifecycle = game, pinproc, lifecycle
        self.Sys11 = False
        self.lastSwitch = None
        self.switch = [False] * 128

    def set_switch(self, number, value):
        if not 0 <= number < 120:
            return False
        return self.SetSwitch(number, value)

    def request(self, fields):
        if fields[0] == 'SW':
            return self.set_switch(int(fields[1]), bool(int(fields[2])))
        return self.lifecycle(fields)

    def outputs(self):
        return tuple([int(bool(value)) for value in values] for values in
                     (self.getCoilStates(), self.getLampStates(), self.getGIStates()))
'''


def build_mapping(source):
    """Return (module bytes, provenance) from one bounded, hash-verified read.

    Only selected method text is parsed. Python 2 imports, logging, registration,
    Run/Stop and module-level code are neither parsed into output nor executed.
    The exact SHA is fixed, not a CLI or policy override.
    """
    path = Path(source)
    with path.open('rb') as stream:
        raw = stream.read(SOURCE_BYTES + 1)
    if len(raw) != SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise MappingError('Unknown/modified register_vpcom.py; exact qualified SHA256 required')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    chunks = []
    for name in METHODS:
        matches = list(re.finditer(r'^    def ' + re.escape(name) + r'\(', text, re.M))
        if len(matches) != 1:
            raise MappingError('Missing/duplicate mapping method: ' + name)
        start = matches[0].start()
        body_start = text.index('\n', matches[0].end()) + 1
        tail = text[body_start:]
        end = re.search(r'^    (?:def |[A-Za-z_]\w*\s*=)|^\S', tail, re.M)
        block = text[start:body_start + end.start()] if end else text[start:]
        try:
            tree = ast.parse(textwrap.dedent(block))
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
                raise MappingError('Unexpected method body: ' + name)
            tree = ast.fix_missing_locations(_Port().visit(tree))
            chunks.append(textwrap.indent(ast.unparse(tree), '    '))
        except SyntaxError as exc:
            raise MappingError('Cannot parse mapping method: ' + name) from exc
    module = ('# Generated locally from caller-supplied register_vpcom.py; DO NOT REDISTRIBUTE.\n'
              '# Source SHA256: ' + SOURCE_SHA256 + '\n' + WRAPPER + '\n\n' + '\n\n'.join(chunks) + '\n').encode('utf-8')
    ast.parse(module)  # Syntax check only; never exec/import/compile-and-run.
    return module, {'schema': 1, 'generator': 'installer.mapping', 'generator_version': GENERATOR_VERSION,
                    'source_sha256': SOURCE_SHA256, 'source_bytes': len(raw),
                    'generated_sha256': hashlib.sha256(module).hexdigest(),
                    'output': 'app/ccc_wpc_mapping.py', 'redistributable': False}
