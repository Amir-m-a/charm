from setuptools import setup
from distutils.core import Command, Extension
from distutils.sysconfig import get_python_lib
import os, platform, sys, shutil, re, fileinput, subprocess

def replaceString(file, searchExp, replaceExp):
    if file is None:
        return
    for line in fileinput.input(file, inplace=1):
        if searchExp in line:
            line = line.replace(searchExp, replaceExp)
        sys.stdout.write(line)

_ext_modules = []

def read_config(file):
    f = open(file, 'r')
    lines = f.read().split('\n')
    config_key = {}
    for e in lines:
        if e.find('=') != -1:
            param = e.split('=', 1)
            config_key[param[0]] = param[1]
    f.close()
    return config_key

def read_version_file():
    try:
        with open('VERSION', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except IOError:
        return '0.0.0'

def read_readme():
    if not os.path.exists('README.md'):
        return ''
    with open('README.md', 'r', encoding='utf-8') as f:
        return f.read()

def run_pkg_config(package, flags):
    try:
        result = subprocess.run(
            ['pkg-config', flags, package],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ''

def get_pkg_config_flags(packages):
    cflags_parts = []
    ldflags_parts = []

    for package in packages:
        cflags = run_pkg_config(package, '--cflags')
        if cflags:
            cflags_parts.append(cflags)

        ldflags = run_pkg_config(package, '--libs-only-L')
        if ldflags:
            ldflags_parts.append(ldflags)

    return dedupe_flags(' '.join(cflags_parts)), dedupe_flags(' '.join(ldflags_parts))

def dedupe_flags(flags_str):
    seen = set()
    result = []
    for flag in flags_str.split():
        if flag not in seen:
            seen.add(flag)
            result.append(flag)
    return ' '.join(result)

def merge_flags(flags1, flags2):
    return dedupe_flags((flags1 or '') + ' ' + (flags2 or ''))

def windows_msys2_prefixes():
    candidates = [
        os.environ.get('MINGW_PREFIX'),
        r'C:\msys64\mingw64',
        r'C:\tools\msys64\mingw64',
    ]

    prefixes = []
    for prefix in candidates:
        if not prefix:
            continue
        prefix = normalize_path_for_windows(prefix)
        if os.path.isdir(prefix) and prefix not in prefixes:
            prefixes.append(prefix)

    return prefixes

def normalize_path_for_windows(path):
    if platform.system() != 'Windows':
        return path

    if path.startswith('/mingw64'):
        return path.replace('/mingw64', r'C:\msys64\mingw64', 1).replace('/', '\\')
    if path.startswith('/mingw32'):
        return path.replace('/mingw32', r'C:\msys64\mingw32', 1).replace('/', '\\')
    if path.startswith('/usr'):
        return path.replace('/usr', r'C:\msys64\usr', 1).replace('/', '\\')

    return path

def flags_to_include_dirs(flags):
    dirs = []
    for s in (flags or '').split():
        if s.startswith('-I') and len(s) > 2:
            path = normalize_path_for_windows(s[2:])
            if path not in dirs:
                dirs.append(path)
    return dirs

def flags_to_library_dirs(flags):
    dirs = []
    for s in (flags or '').split():
        if s.startswith('-L') and len(s) > 2:
            path = normalize_path_for_windows(s[2:])
            if path not in dirs:
                dirs.append(path)
    return dirs

def flags_to_runtime_library_dirs(flags):
    if platform.system() == 'Windows':
        return []

    dirs = []
    for s in (flags or '').split():
        if s.lower().startswith('-wl,-rpath,'):
            path = s[11:]
            if path not in dirs:
                dirs.append(path)
    return dirs

def append_unique(items, values):
    for value in values:
        if value and value not in items:
            items.append(value)

def get_fallback_paths():
    system = platform.system()
    ldflags_parts = []
    cflags_parts = []

    if system == 'Darwin':
        for prefix in ['/opt/homebrew', '/usr/local']:
            if os.path.exists(prefix):
                lib_path = os.path.join(prefix, 'lib')
                inc_path = os.path.join(prefix, 'include')
                if os.path.isdir(lib_path):
                    ldflags_parts.append(f'-L{lib_path}')
                if os.path.isdir(inc_path):
                    cflags_parts.append(f'-I{inc_path}')
                break

    elif system == 'Linux':
        for lib_path in ['/usr/local/lib', '/usr/lib', '/usr/lib/x86_64-linux-gnu']:
            if os.path.isdir(lib_path):
                ldflags_parts.append(f'-L{lib_path}')

        for inc_path in ['/usr/local/include', '/usr/include']:
            if os.path.isdir(inc_path):
                cflags_parts.append(f'-I{inc_path}')

    elif system == 'Windows':
        for prefix in windows_msys2_prefixes():
            inc_path = os.path.join(prefix, 'include')
            lib_path = os.path.join(prefix, 'lib')

            if os.path.isdir(inc_path):
                cflags_parts.append(f'-I{inc_path}')
            if os.path.isdir(lib_path):
                ldflags_parts.append(f'-L{lib_path}')

    return ' '.join(cflags_parts), ' '.join(ldflags_parts)

def get_default_config():
    config = {
        'PAIR_MOD': 'yes',
        'USE_PBC': 'yes',
        'INT_MOD': 'yes',
        'ECC_MOD': 'yes',
        'LAT_MOD': 'no',
        'DISABLE_BENCHMARK': 'no',
        'LDFLAGS': '',
        'CPPFLAGS': '',
        'CHARM_CFLAGS': '',
        'VERSION': read_version_file(),
    }

    required_packages = ['gmp', 'pbc', 'libcrypto']

    pkg_cflags, pkg_ldflags = get_pkg_config_flags(required_packages)
    fallback_cflags, fallback_ldflags = get_fallback_paths()

    if pkg_cflags or pkg_ldflags:
        print("Using pkg-config for library detection with fallback paths merged")
        config['CPPFLAGS'] = merge_flags(pkg_cflags, fallback_cflags)
        config['LDFLAGS'] = merge_flags(pkg_ldflags, fallback_ldflags)
    else:
        print("pkg-config not available, using fallback paths")
        config['CPPFLAGS'] = fallback_cflags
        config['LDFLAGS'] = fallback_ldflags

    return config

def merge_environment_flags(opt):
    env_cppflags = merge_flags(os.environ.get('CPPFLAGS', ''), os.environ.get('CFLAGS', ''))
    env_ldflags = os.environ.get('LDFLAGS', '')

    opt['CPPFLAGS'] = merge_flags(opt.get('CPPFLAGS', ''), env_cppflags)
    opt['CHARM_CFLAGS'] = merge_flags(opt.get('CHARM_CFLAGS', ''), os.environ.get('CHARM_CFLAGS', ''))
    opt['LDFLAGS'] = merge_flags(opt.get('LDFLAGS', ''), env_ldflags)

    fallback_cflags, fallback_ldflags = get_fallback_paths()
    opt['CPPFLAGS'] = merge_flags(opt.get('CPPFLAGS', ''), fallback_cflags)
    opt['LDFLAGS'] = merge_flags(opt.get('LDFLAGS', ''), fallback_ldflags)

    return opt

print("Platform:", platform.system())

config = os.environ.get('CONFIG_FILE')
opt = {}

if config is not None:
    print("Config file:", config)
    opt = read_config(config)
else:
    config = "config.mk"
    print("Config file:", config)
    try:
        opt = read_config(config)
    except IOError:
        print("Warning, using default config values.")
        print("You probably want to run ./configure.sh first.")
        print("Using platform-aware defaults for PyPI installation...")
        opt = get_default_config()

opt = merge_environment_flags(opt)

if os.environ.get('LAT_MOD', '').lower() in ('yes', '1', 'true'):
    opt['LAT_MOD'] = 'yes'

if os.environ.get('DISABLE_BENCHMARK', '').lower() in ('yes', '1', 'true'):
    opt['DISABLE_BENCHMARK'] = 'yes'

core_path = 'charm/core/'
math_path = core_path + 'math/'
crypto_path = core_path + 'crypto/'
utils_path = core_path + 'utilities/'
benchmark_path = core_path + "benchmark/"
cryptobase_path = crypto_path + "cryptobase/"

core_prefix = 'charm.core'
math_prefix = core_prefix + '.math'
crypto_prefix = core_prefix + '.crypto'

if opt.get('DISABLE_BENCHMARK') == 'yes':
    _macros = None
    _undef_macro = ['BENCHMARK_ENABLED']
else:
    _macros = [('BENCHMARK_ENABLED', '1')]
    _undef_macro = None

if opt.get('USE_PBC') == 'yes':
    pass
elif opt.get('USE_RELIC') == 'yes':
    relic_lib = "/usr/local/lib/librelic_s.a"
    relic_inc = "/usr/local/include/relic"
elif opt.get('USE_MIRACL') == 'yes' and opt.get('MIRACL_MNT') == 'yes':
    mnt_opt = [('BUILD_MNT_CURVE', '1'), ('BUILD_BN_CURVE', '0'), ('BUILD_SS_CURVE', '0')]
    if _macros:
        _macros.extend(mnt_opt)
    else:
        _macros = mnt_opt
    miracl_lib = "/usr/local/lib/miracl-mnt.a"
    miracl_inc = "/usr/local/include/miracl"
elif opt.get('USE_MIRACL') == 'yes' and opt.get('MIRACL_BN') == 'yes':
    bn_opt = [('BUILD_MNT_CURVE', '0'), ('BUILD_BN_CURVE', '1'), ('BUILD_SS_CURVE', '0')]
    if _macros:
        _macros.extend(bn_opt)
    else:
        _macros = bn_opt
    miracl_lib = "/usr/local/lib/miracl-bn.a"
    miracl_inc = "/usr/local/include/miracl"
elif opt.get('USE_MIRACL') == 'yes' and opt.get('MIRACL_SS') == 'yes':
    ss_opt = [('BUILD_MNT_CURVE', '0'), ('BUILD_BN_CURVE', '0'), ('BUILD_SS_CURVE', '1')]
    if _macros:
        _macros.extend(ss_opt)
    else:
        _macros = ss_opt
    miracl_lib = "/usr/local/lib/miracl-ss.a"
    miracl_inc = "/usr/local/include/miracl"
else:
    sys.exit("Need to select which module to build for pairing.")

_charm_version = opt.get('VERSION') or read_version_file()

lib_config_file = 'charm/config.py'

inc_dirs = []
append_unique(inc_dirs, flags_to_include_dirs(opt.get('CHARM_CFLAGS', '')))
append_unique(inc_dirs, flags_to_include_dirs(opt.get('CPPFLAGS', '')))

library_dirs = []
append_unique(library_dirs, flags_to_library_dirs(opt.get('LDFLAGS', '')))

runtime_library_dirs = flags_to_runtime_library_dirs(opt.get('LDFLAGS', ''))

print("Include dirs:", inc_dirs)
print("Library dirs:", library_dirs)

if opt.get('PAIR_MOD') == 'yes':
    if opt.get('USE_PBC') == 'yes':
        replaceString(lib_config_file, "pairing_lib=libs ", "pairing_lib=libs.pbc")
        pairing_module = Extension(
            math_prefix + '.pairing',
            include_dirs=[utils_path, benchmark_path] + inc_dirs,
            sources=[
                math_path + 'pairing/pairingmodule.c',
                utils_path + 'base64.c'
            ],
            libraries=['pbc', 'gmp', 'crypto'],
            define_macros=_macros,
            undef_macros=_undef_macro,
            library_dirs=library_dirs,
            runtime_library_dirs=runtime_library_dirs
        )

    elif opt.get('USE_RELIC') == 'yes':
        replaceString(lib_config_file, "pairing_lib=libs ", "pairing_lib=libs.relic")
        pairing_module = Extension(
            math_prefix + '.pairing',
            include_dirs=[utils_path, benchmark_path, relic_inc] + inc_dirs,
            sources=[
                math_path + 'pairing/relic/pairingmodule3.c',
                math_path + 'pairing/relic/relic_interface.c',
                utils_path + 'base64.c'
            ],
            libraries=['relic', 'gmp', 'crypto'],
            define_macros=_macros,
            undef_macros=_undef_macro,
            library_dirs=library_dirs,
            runtime_library_dirs=runtime_library_dirs
        )

    elif opt.get('USE_MIRACL') == 'yes':
        replaceString(lib_config_file, "pairing_lib=libs ", "pairing_lib=libs.miracl")
        pairing_module = Extension(
            math_prefix + '.pairing',
            include_dirs=[utils_path, benchmark_path, miracl_inc] + inc_dirs,
            sources=[
                math_path + 'pairing/miracl/pairingmodule2.c',
                math_path + 'pairing/miracl/miracl_interface2.cc'
            ],
            libraries=['gmp', 'crypto', 'stdc++'],
            define_macros=_macros,
            undef_macros=_undef_macro,
            extra_objects=[miracl_lib],
            extra_compile_args=None,
            library_dirs=library_dirs,
            runtime_library_dirs=runtime_library_dirs
        )

    _ext_modules.append(pairing_module)

if opt.get('INT_MOD') == 'yes':
    replaceString(lib_config_file, "int_lib=libs ", "int_lib=libs.gmp")
    integer_module = Extension(
        math_prefix + '.integer',
        include_dirs=[utils_path, benchmark_path] + inc_dirs,
        sources=[
            math_path + 'integer/integermodule.c',
            utils_path + 'base64.c'
        ],
        libraries=['gmp', 'crypto'],
        define_macros=_macros,
        undef_macros=_undef_macro,
        library_dirs=library_dirs,
        runtime_library_dirs=runtime_library_dirs
    )
    _ext_modules.append(integer_module)

if opt.get('ECC_MOD') == 'yes':
    replaceString(lib_config_file, "ec_lib=libs ", "ec_lib=libs.openssl")
    ecc_module = Extension(
        math_prefix + '.elliptic_curve',
        include_dirs=[utils_path, benchmark_path] + inc_dirs,
        sources=[
            math_path + 'elliptic_curve/ecmodule.c',
            utils_path + 'base64.c'
        ],
        libraries=['gmp', 'crypto'],
        define_macros=_macros,
        undef_macros=_undef_macro,
        library_dirs=library_dirs,
        runtime_library_dirs=runtime_library_dirs
    )
    _ext_modules.append(ecc_module)

if opt.get('LAT_MOD') == 'yes':
    replaceString(lib_config_file, "lattice_lib=libs ", "lattice_lib=libs.ntl")

    ntl_inc_dirs = list(inc_dirs)
    ntl_lib_dirs = list(library_dirs)
    ntl_rt_dirs = list(runtime_library_dirs)

    try:
        _ntl_cflags = subprocess.check_output(
            ['pkg-config', '--cflags', 'ntl'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        _ntl_libs = subprocess.check_output(
            ['pkg-config', '--libs', 'ntl'],
            stderr=subprocess.DEVNULL
        ).decode().strip()

        append_unique(ntl_inc_dirs, flags_to_include_dirs(_ntl_cflags))
        append_unique(ntl_lib_dirs, flags_to_library_dirs(_ntl_libs))
    except (subprocess.CalledProcessError, FileNotFoundError):
        prefixes = ['/opt/homebrew/opt/ntl', '/usr/local', '/usr']

        if platform.system() == 'Windows':
            prefixes = windows_msys2_prefixes() + prefixes

        for prefix in prefixes:
            if os.path.isfile(os.path.join(prefix, 'include', 'NTL', 'ZZ.h')):
                append_unique(ntl_inc_dirs, [os.path.join(prefix, 'include')])
                append_unique(ntl_lib_dirs, [os.path.join(prefix, 'lib')])
                break

    lattice_libraries = ['ntl', 'gmp']
    if platform.system() != 'Windows':
        lattice_libraries.append('pthread')

    lattice_compile_args = ['/std:c++14'] if platform.system() == 'Windows' else ['-std=c++14']

    lattice_module = Extension(
        math_prefix + '.lattice',
        include_dirs=[utils_path, benchmark_path, math_path + 'lattice/'] + ntl_inc_dirs,
        sources=[math_path + 'lattice/latticemodule.cpp'],
        libraries=lattice_libraries,
        define_macros=_macros,
        undef_macros=_undef_macro,
        library_dirs=ntl_lib_dirs,
        runtime_library_dirs=ntl_rt_dirs,
        language='c++',
        extra_compile_args=lattice_compile_args
    )
    _ext_modules.append(lattice_module)

benchmark_module = Extension(
    core_prefix + '.benchmark',
    sources=[benchmark_path + 'benchmarkmodule.c']
)

cryptobase = Extension(
    crypto_prefix + '.cryptobase',
    sources=[cryptobase_path + 'cryptobasemodule.c']
)

aes = Extension(
    crypto_prefix + '.AES',
    include_dirs=[cryptobase_path],
    sources=[crypto_path + 'AES/AES.c']
)

des = Extension(
    crypto_prefix + '.DES',
    include_dirs=[cryptobase_path + 'libtom/', cryptobase_path],
    sources=[crypto_path + 'DES/DES.c']
)

des3 = Extension(
    crypto_prefix + '.DES3',
    include_dirs=[cryptobase_path + 'libtom/', cryptobase_path, crypto_path + 'DES/'],
    sources=[crypto_path + 'DES3/DES3.c']
)

aesgcm = Extension(
    crypto_prefix + '.AES_GCM',
    include_dirs=inc_dirs,
    sources=[crypto_path + 'AES_GCM/AES_GCM.c'],
    libraries=['crypto'],
    library_dirs=library_dirs,
    runtime_library_dirs=runtime_library_dirs
)

_ext_modules.extend([benchmark_module, cryptobase, aes, des, des3, aesgcm])

if platform.system() in ['Linux', 'Windows']:
    if opt.get('DISABLE_BENCHMARK') != 'yes':
        if opt.get('PAIR_MOD') == 'yes':
            pairing_module.sources.append(benchmark_path + 'benchmarkmodule.c')
        if opt.get('INT_MOD') == 'yes':
            integer_module.sources.append(benchmark_path + 'benchmarkmodule.c')
        if opt.get('ECC_MOD') == 'yes':
            ecc_module.sources.append(benchmark_path + 'benchmarkmodule.c')

setup(
    name='charm-crypto-framework',
    version=_charm_version,
    description='Charm is a framework for rapid prototyping of cryptosystems',
    long_description=read_readme(),
    long_description_content_type='text/markdown',
    ext_modules=_ext_modules,
    author="J. Ayo Akinyele",
    author_email="jakinye3@jhu.edu",
    url="https://charm-crypto.io/",
    project_urls={
        "Documentation": "https://charm-crypto.io/documentation",
        "Repository": "https://github.com/JHUISI/charm",
        "Issues": "https://github.com/JHUISI/charm/issues",
    },
    python_requires='>=3.8',
    packages=[
        'charm',
        'charm.core',
        'charm.core.crypto',
        'charm.core.engine',
        'charm.core.math',
        'charm.test',
        'charm.test.schemes',
        'charm.test.toolbox',
        'charm.toolbox',
        'charm.zkp_compiler',
        'charm.schemes',
        'charm.schemes.ibenc',
        'charm.schemes.abenc',
        'charm.schemes.pkenc',
        'charm.schemes.hibenc',
        'charm.schemes.pksig',
        'charm.schemes.commit',
        'charm.schemes.grpsig',
        'charm.schemes.prenc',
        'charm.adapters',
    ],
    license='LGPL-3.0-or-later',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Programming Language :: C',
        'Topic :: Security :: Cryptography',
    ]
)
