# Copyright (c) 2026 Komesu, D.K.
# Licensed under the MIT License.

"""Internal field-name constants used while parsing BCB SGS HTML.

These constants exist so the parsers can produce intermediate dicts keyed
with stable identifiers regardless of the Portuguese/English labels in
the source HTML. The public API exposes
:mod:`bcb_sgs_fetcher.models` dataclasses instead — these constants are
implementation details and not part of the package's stable interface.
"""

SERIES_ID = "series_id"
SERIES_NAME_INDEX = "serie_indice"
SERIES_NAME = "serie"
SERIES_NAME_EN = "serie_ingles"
SERIES_NAME_ABBR = "serie_abrev"
SERIES_NAME_EN_ABBR = "serie_abrev_ingles"
DATE = "data"
START = "inicio"
END = "fim"
UNIT_NAME = "unidade"
FREQUENCY_ACRONYM = "frequencia_acronimo"
FREQUENCY = "frequencia"
PRECISION = "precisao"
MAX_VALUE = "max_value"
MIN_VALUE = "min_value"
MANAGER_OWNER = "gestor_proprietario"
SOURCE = "fonte"
SERIES_TYPE = "tipo_serie"
THEME_HIERARCHY = "hierarquia_tema"
SPECIAL = "especial"
FORMULA = "formula"
PRIMITIVE_SERIES = "serie_primitiva"
LAST_UPDATE = "last_update"
VALUE = "valor"
WARNING_MESSAGE = "mensagem_aviso"
MESSAGE = "message"
ACTIVE = "ativo"

BASIC = "basic"
FULL = "full"
FULL_PROVIDER_DATA = "provider_data"
FULL_DESCRIPTION = "description"
FULL_METHODOLOGY = "methodology"
FULL_DISSEMINATION_FORMATS = "dissemination_formats"
