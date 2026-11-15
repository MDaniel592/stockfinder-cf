"""Catalog-coverage policy for PcComponentes and Coolmod discovery.

The category API mixes current stock with decade-old references. Owner policy
(2026-11-08), shared by both shops:

* CPU: AMD Ryzen 7000/8000/9000 desktop (AM5), AMD Ryzen 1000-5000 desktop
  (AM4, owner decision 2026-11-09), Intel Core 13th/14th gen (LGA1700) and
  Core Ultra 200S (LGA1851). Older Intel, tray and laptop parts are rejected.
* GPU: only NVIDIA RTX 50, AMD RX 9000 and Intel Arc A/B-series (Arc A added
  by owner decision 2026-11-09).
* Storage: internal SATA SSDs, HDDs and NVMe SSDs. NVMe drives pass at any PCIe
  generation. External, USB, SAS, IDE and tape rejected.
* PSU: every desktop unit of at least 500 W, ATX 3.x or older (the web filters
  by the `Estándar ATX` spec).
* Motherboards (AM5/LGA1700/LGA1851 chipsets), RAM (DDR4/DDR5), cases and
  coolers keep their previous rules.

Rules are heuristics over name + normalized specs; they are meant to be
reviewed, not to authorize publication. Existing products of older generations
are never touched by discovery; the rules only decide what gets staged.
"""
import re

PERIPHERALS = {'monitores-pc', 'teclados', 'ratones'}
COMPONENTS = {'procesadores', 'placas-base', 'tarjetas-graficas', 'memorias-ram',
              'fuentes-alimentacion', 'discos-duros', 'cajas-pc',
              'ventiladores-cpu', 'refrigeracion-liquida'}

CPU_OLD = re.compile(r'\b(xeon|epyc|threadripper|opteron|athlon|pentium|celeron)\b', re.I)
CPU_EXCLUDE = re.compile(r'\b(tray|port[aá]til|laptop|mobile|es/qs|engineering sample|'
                         r'hikvision|ds-dt|controlador|video ?wall)\b', re.I)
# Desktop suffixes are whitelisted so mobile parts (H, HS, HX, U, V, P...) fail
# the trailing word boundary.
CPU_MODERN = [
    (re.compile(r'\bryzen\s+[3579]\s+(?:pro\s+)?[789]\d{3}(?:x3d2?|xt|x|gt|ge|g|f|e)?\b', re.I),
     'amd-am5-ryzen-7000/8000/9000'),
    (re.compile(r'\bryzen\s+[3579]\s+(?:pro\s+)?[1-5]\d{3}(?:x3d|xt|x|gt|ge|g|f|e|t)?\b', re.I),
     'amd-am4-ryzen-1000-5000'),
    (re.compile(r'\bcore\s+ultra\s+[3579]\s+2\d{2}(?:kf|k|f|t)?\b', re.I), 'intel-200s'),
    (re.compile(r'\bi[3579]-1[34]\d{3}(?:kf|ks|k|f|te|t)?\b', re.I), 'intel-13/14'),
]

GPU_EXCLUDE = re.compile(r'\b(quadro|tesla|gtx|gt\s+\d{3}|rtx\s+a\d|rtx\s+pro|rtx\s+5000\b|l40s?|'
                         r'port[aá]til|laptop|mobile|workstation|arc\s+pro|'
                         r'estaciones?\s+de\s+trabajo)\b', re.I)
GPU_MODERN = [
    (re.compile(r'\brtx\s+50\d{2}\b', re.I), 'nvidia-50'),
    (re.compile(r'\brx\s+9\d{3}\b', re.I), 'amd-9000'),
    (re.compile(r'\barc\s+b\d{3}\b', re.I), 'intel-arc-b'),
    (re.compile(r'\barc\s+a\d{3}m?\b', re.I), 'intel-arc-a'),
]

BOARD_MODERN = re.compile(
    r'\b(a620|b650e?|x670e?|b840|b850|x870e?|b860|z890|h810|w880|b760|z790|h770|w680)'
    r'[a-z0-9-]*\b', re.I)
BOARD_OLD = re.compile(
    r'\b(a520|b450|b550|x470|x570|h410|h470|b460|h510|b560|z590|w480|w580|'
    r'h610|b660|z690|b365|z390|h310|b360|z370|h370|x299|x399)[a-z0-9-]*\b', re.I)
BOARD_EXCLUDE = re.compile(r'\b(port[aá]til|so-?dimm|servidor|epyc|xeon|'
                           r'c621|c741|sp5|lga\s*(3647|4189|4677))\b', re.I)

RAM_INCLUDE = re.compile(r'\bddr[45]\b', re.I)
RAM_OLD = re.compile(r'\bddr[23]\b', re.I)
RAM_SPEED = re.compile(r'(\d{3,5})\s*(?:mhz|mt/s)', re.I)
RAM_EXCLUDE = re.compile(r'\b(so-?dimm|ecc|registrad[ao]|servidor|port[aá]til)\b', re.I)

PSU_ATX3 = re.compile(r'\b(atx\s*3\.[01]|pcie\s*5(?:\.\d)?|12v-?2x6|12vhpwr)\b', re.I)
PSU_EXCLUDE = re.compile(r'\b(sai|ups|cargador|adaptador|para\s+(dell|hp|lenovo|asus|acer)|'
                         r'port[aá]til|reacondicionad)\b', re.I)

# Storage. Anything external or non-SATA/NVMe-attached is out; the rest of the
# `discos-duros` listing (SATA SSD, NVMe SSD, HDD) is in.
STORAGE_EXCLUDE = re.compile(
    r'\b(usb|thunderbolt|extern[oa]s?|sas|ide|scsi|lto|ultrium|cinta|tape|controlador|raid|'
    r'impresora|lector|carcasa|enclosure|docking|dock|adaptador|cable|reacondicionad[ao]|'
    r'para\s+servidor|nas\s+de\s+\d+\s+bah[ií]as?)\b', re.I)
STORAGE_SSD = re.compile(r'\b(ssd|nvme|solid\s+state|estado\s+s[oó]lido)\b|\bm\.2\b', re.I)
STORAGE_EVIDENCE = re.compile(r'\b(sata\d*|ssd|nvme|hdd|rpm|disco\s+duro|mec[aá]nico)\b|\bm\.2\b|'
                              r'\b[23]\.5\b', re.I)

COOLING_MODERN_SOCKET = re.compile(r'\b(am5|lga\s*1851|lga\s*1700)\b', re.I)
COOLING_ANY_SOCKET = re.compile(
    r'\b(am[234]\+?|lga\s*(115[0-9]|1200|1700|1851)|tr4|str5)\b', re.I)
COOLING_EXCLUDE = re.compile(r'\b(port[aá]til|mobile)\b', re.I)

CASE_EXCLUDE = re.compile(r'\b(servidor|rack|reacondicionad)\b', re.I)


def text_of(name, specs=None):
    parts = [name or '']
    for spec in specs or []:
        if isinstance(spec, dict):
            parts.append(f"{spec.get('name') or ''} {spec.get('value') or ''}")
    return ' '.join(parts)


def first_match(patterns, text):
    for pattern in patterns:
        if pattern.search(text):
            return pattern
    return None


def classify(category, name, specs=None):
    """Return {'modern': bool, 'generation': str|None, 'reason': str}."""
    text = text_of(name, specs)
    if category in PERIPHERALS:
        return {'modern': False, 'generation': None, 'reason': 'peripheral'}

    if category == 'procesadores':
        if CPU_OLD.search(text):
            return {'modern': False, 'generation': None, 'reason': 'cpu-enterprise-or-legacy'}
        if CPU_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'cpu-oem-or-laptop'}
        for pattern, generation in CPU_MODERN:
            if pattern.search(text):
                return {'modern': True, 'generation': generation, 'reason': 'cpu-latest-generation'}
        return {'modern': False, 'generation': None, 'reason': 'cpu-older-generation'}

    if category == 'tarjetas-graficas':
        if GPU_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'gpu-professional-or-legacy'}
        for pattern, generation in GPU_MODERN:
            if pattern.search(text):
                return {'modern': True, 'generation': generation, 'reason': 'gpu-latest-generation'}
        return {'modern': False, 'generation': None, 'reason': 'gpu-older-generation'}

    if category == 'placas-base':
        if BOARD_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'board-laptop-or-server'}
        if BOARD_OLD.search(text):
            return {'modern': False, 'generation': None, 'reason': 'board-am4-or-lga1200/600'}
        match = BOARD_MODERN.search(text)
        if match:
            return {'modern': True, 'generation': match.group(1).upper(), 'reason': 'board-current-or-previous'}
        return {'modern': False, 'generation': None, 'reason': 'board-unknown-chipset'}

    if category == 'memorias-ram':
        if RAM_OLD.search(text):
            return {'modern': False, 'generation': None, 'reason': 'ram-ddr3-or-older'}
        if RAM_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'ram-laptop-or-server'}
        match = RAM_INCLUDE.search(text)
        if match:
            return {'modern': True, 'generation': match.group(0).upper(), 'reason': 'ram-ddr4-allowed/ddr5'}
        speed = RAM_SPEED.search(text)
        if speed:
            mhz = int(speed.group(1))
            if mhz >= 4800:
                return {'modern': True, 'generation': 'DDR5', 'reason': 'ram-ddr5-inferred'}
            if mhz >= 2133:
                return {'modern': True, 'generation': 'DDR4', 'reason': 'ram-ddr4-inferred'}
        return {'modern': False, 'generation': None, 'reason': 'ram-unknown-standard'}

    if category == 'fuentes-alimentacion':
        if PSU_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'psu-not-a-desktop-atx-unit'}
        watts = [int(value) for value in re.findall(r'\b(\d{2,4})\s*w\b', text, re.I)]
        if watts and max(watts) < 500:
            return {'modern': False, 'generation': None, 'reason': 'psu-under-500w'}
        if PSU_ATX3.search(text):
            return {'modern': True, 'generation': 'ATX3', 'reason': 'psu-atx3/pcie5'}
        return {'modern': True, 'generation': 'ATX2', 'reason': 'psu-pre-atx3-allowed'}

    if category == 'discos-duros':
        if STORAGE_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'storage-external-sas-or-not-a-drive'}
        if not STORAGE_EVIDENCE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'storage-unrecognized'}
        if re.search(r'\bnvme\b|\bpci-?e\b', text, re.I):
            return {'modern': True, 'generation': 'NVMe SSD', 'reason': 'storage-nvme'}
        if STORAGE_SSD.search(text) and not re.search(r'\bhdd\b|\brpm\b', text, re.I):
            return {'modern': True, 'generation': 'SATA SSD' if re.search(r'\bsata', text, re.I) else 'SSD',
                    'reason': 'storage-sata-ssd'}
        return {'modern': True, 'generation': 'HDD', 'reason': 'storage-hdd'}

    if category in ('ventiladores-cpu', 'refrigeracion-liquida'):
        if COOLING_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'cooling-laptop'}
        if COOLING_MODERN_SOCKET.search(text):
            return {'modern': True, 'generation': 'AM5/LGA1700/1851', 'reason': 'cooling-modern-socket'}
        if COOLING_ANY_SOCKET.search(text):
            return {'modern': False, 'generation': None, 'reason': 'cooling-only-old-sockets'}
        return {'modern': True, 'generation': None, 'reason': 'cooling-socket-unspecified'}

    if category == 'cajas-pc':
        if CASE_EXCLUDE.search(text):
            return {'modern': False, 'generation': None, 'reason': 'case-server-or-refurbished'}
        return {'modern': True, 'generation': None, 'reason': 'case-current-listing'}

    return {'modern': False, 'generation': None, 'reason': 'category-not-components'}
