# -*- coding: utf-8 -*-
import json, os, re, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import jikuai
from jikuai import keywords

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EXT_DIR = os.path.join(REPO_ROOT, 'editors', 'vscode')
PACKAGE_JSON = os.path.join(EXT_DIR, 'package.json')
GRAMMAR_JSON = os.path.join(EXT_DIR, 'syntaxes', '极快.tmLanguage.json')
LANG_CONFIG_JSON = os.path.join(EXT_DIR, 'language-configuration.json')
EXTENSION_TS = os.path.join(EXT_DIR, 'src', 'extension.ts')

def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

@pytest.fixture(scope='module')
def pkg():
    return _load_json(PACKAGE_JSON)

@pytest.fixture(scope='module')
def grammar():
    return _load_json(GRAMMAR_JSON)

def _collect_matches(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ('match', 'begin', 'end') and isinstance(value, str):
                out.append(value)
            else:
                _collect_matches(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_matches(item, out)
    return out

@pytest.fixture(scope='module')
def grammar_regexes(grammar):
    return _collect_matches(grammar, [])

def _repo_regex(grammar, name, index=0):
    return grammar['repository'][name]['patterns'][index]['match']

def test_extension_files_exist():
    for path in (PACKAGE_JSON, GRAMMAR_JSON, LANG_CONFIG_JSON, EXTENSION_TS,
                 os.path.join(EXT_DIR, 'tsconfig.json'),
                 os.path.join(EXT_DIR, 'README.md'),
                 os.path.join(EXT_DIR, 'CHANGELOG.md'),
                 os.path.join(EXT_DIR, '.vscodeignore')):
        assert os.path.isfile(path), path

def test_package_json_is_valid_json(pkg):
    assert isinstance(pkg, dict)
    assert pkg['name'] == 'jikuai-vscode'

def test_package_version_matches_main_package(pkg):
    assert pkg['version'] == '0.6.0'
    assert pkg['version'] == jikuai.__version__

def test_package_engines_and_activation(pkg):
    assert pkg['engines']['vscode'] == '^1.75.0'
    assert pkg['activationEvents'] == ['onLanguage:jikuai']
    assert pkg['main'] == './out/extension.js'

def test_package_scripts_and_deps(pkg):
    assert pkg['scripts']['compile'] == 'tsc -p ./'
    assert pkg['scripts']['vscode:prepublish'] == 'npm run compile'
    assert pkg['dependencies'] == {'vscode-languageclient': '^9.0.0'}
    for dev in ('typescript', '@types/vscode', '@types/node'):
        assert dev in pkg['devDependencies'], dev

def test_contributes_languages(pkg):
    langs = pkg['contributes']['languages']
    assert isinstance(langs, list) and len(langs) == 1
    lang = langs[0]
    assert lang['id'] == 'jikuai'
    assert lang['extensions'] == ['.jk']

def test_contributes_grammars_path_exists(pkg):
    grammars = pkg['contributes']['grammars']
    g = grammars[0]
    assert g['scopeName'] == 'source.jikuai'
    rel = g['path'].lstrip('./')
    assert os.path.isfile(os.path.join(EXT_DIR, rel))

def test_contributes_configuration_properties(pkg):
    props = pkg['contributes']['configuration']['properties']
    assert props['极快.pythonPath']['default'] == 'python'
    assert props['极快.lsp.enabled']['default'] is True

def test_grammar_scope_and_patterns(grammar):
    assert grammar['scopeName'] == 'source.jikuai'
    assert grammar['fileTypes'] == ['jk']
    assert len(grammar['patterns']) > 0

def test_grammar_top_level_includes_resolve(grammar):
    repo = grammar['repository']
    for item in grammar['patterns']:
        include = item.get('include')
        assert include and include.startswith('#')
        assert include[1:] in repo

def test_grammar_required_scope_names(grammar):
    raw = json.dumps(grammar, ensure_ascii=False)
    for scope in ('comment.line.number-sign.jikuai', 'comment.line.double-dash.jikuai',
                  'string.quoted.double.jikuai', 'keyword.control.jikuai',
                  'support.function.builtin.jikuai', 'keyword.operator.adverb.jikuai',
                  'constant.language.jikuai', 'constant.numeric.jikuai',
                  'constant.numeric.currency.jikuai', 'support.type.jikuai'):
        assert scope in raw, scope

def test_grammar_regexes_compile(grammar_regexes):
    assert grammar_regexes
    for pattern in grammar_regexes:
        re.compile(pattern)

def test_grammar_covers_all_keywords(grammar_regexes):
    declared = '|'.join(grammar_regexes)
    missing = sorted(kw for kw in keywords.ALL_KEYWORDS if kw not in declared)
    assert not missing, missing

def test_grammar_covers_sampled_verbs(grammar_regexes):
    declared = '|'.join(grammar_regexes)
    for v in ('加','减','乘','除','打印','人民币',
              '大写金额','校验身份证',
              '大于等于','小于等于'):
        assert v in declared, v
    for a in keywords.ADVERBS:
        assert a in declared, a
    for t in keywords.BUILTIN_TYPES:
        assert t in declared, t

def test_keyword_regex_is_precise(grammar):
    kw_re = re.compile(_repo_regex(grammar, 'keywords', 0))
    assert kw_re.match('如果 ') is not None
    assert kw_re.match('定义') is not None
    assert kw_re.match('如果然') is None
    assert kw_re.match('返回值') is None
    assert kw_re.match('否则如果 ').group(0) == '否则如果'

def test_builtin_regex_longest_first(grammar):
    long_re = re.compile(_repo_regex(grammar, 'builtins', 0))
    assert long_re.match('大写金额 ').group(0) == '大写金额'
    assert long_re.match('大于等于 ').group(0) == '大于等于'

def test_number_and_currency_regex(grammar):
    num_re = re.compile(_repo_regex(grammar, 'numbers', 0))
    assert num_re.match('123').group(0) == '123'
    assert num_re.match('3.14').group(0) == '3.14'
    cur_re = re.compile(_repo_regex(grammar, 'currency', 0))
    assert cur_re.match('￥100').group(0) == '￥100'
    assert cur_re.match('100') is None

def test_extension_ts_wires_lsp_server():
    with open(EXTENSION_TS, encoding='utf-8') as f:
        src = f.read()
    assert 'vscode-languageclient/node' in src
    assert 'TransportKind.stdio' in src
    assert '极快' in src
    assert 'lsp.enabled' in src
    assert '未找到 jikuai_lsp' in src
    assert 'export async function activate' in src
    assert 'export async function deactivate' in src

def test_language_configuration_structure():
    cfg = _load_json(LANG_CONFIG_JSON)
    assert cfg['comments']['lineComment'] == '--'
    brackets = set(tuple(pair) for pair in cfg['brackets'])
    assert ('（', '）') in brackets
    assert ('(', ')') in brackets

def test_tsconfig_options():
    cfg = _load_json(os.path.join(EXT_DIR, 'tsconfig.json'))
    opts = cfg['compilerOptions']
    assert opts['target'] == 'ES2020'
    assert opts['module'] == 'commonjs'
    assert opts['strict'] is True

def test_vscodeignore_excludes_sources():
    with open(os.path.join(EXT_DIR, '.vscodeignore'), encoding='utf-8') as f:
        lines = set(line.strip() for line in f if line.strip())
    assert 'src/**' in lines
    assert 'node_modules/**' in lines

def test_changelog_has_0_6_0_entry(pkg):
    with open(os.path.join(EXT_DIR, 'CHANGELOG.md'), encoding='utf-8') as f:
        text = f.read()
    assert '[0.6.0]' in text

def test_readme_documents_install_build_and_scope():
    with open(os.path.join(EXT_DIR, 'README.md'), encoding='utf-8') as f:
        text = f.read()
    assert 'pip install -e lsp/' in text
    assert 'npm run compile' in text
    assert '极快.pythonPath' in text
    assert 'M6' in text
