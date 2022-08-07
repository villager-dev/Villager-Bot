from enum import IntEnum, auto


class PacketType(IntEnum):
    # special packet types handled directly by the Server/Client classes
    AUTH = auto()
    BROADCAST_REQUEST = auto()

    # other regular packet types
    FETCH_SHARD_IDS = auto()
    EXEC_CODE = auto()
    COOLDOWN_CHECK_ADD = auto()
    COOLDOWN_ADD = auto()
    COOLDOWN_RESET = auto()
    DM_MESSAGE = auto()
    MINE_COMMAND = auto()
    MINE_COMMANDS_RESET = auto()
    CONCURRENCY_CHECK = auto()
    CONCURRENCY_ACQUIRE = auto()
    CONCURRENCY_RELEASE = auto()
    COMMAND_RAN = auto()
    REMINDER = auto()
    FETCH_STATS = auto()
    TRIVIA = auto()
    UPDATE_SUPPORT_SERVER_ROLES = auto()
    RELOAD_DATA = auto()
    ECON_PAUSE = auto()
    ECON_PAUSE_UNDO = auto()
    ECON_PAUSE_CHECK = auto()
    ACTIVE_FX_CHECK = auto()
    ACTIVE_FX_ADD = auto()
    ACTIVE_FX_REMOVE = auto()
    TOPGG_VOTE = auto()
    DB_EXEC = auto()
    DB_EXEC_MANY = auto()
    DB_FETCH_VAL = auto()
    DB_FETCH_ROW = auto()
    DB_FETCH_ALL = auto()
    GET_USER_NAME = auto()
