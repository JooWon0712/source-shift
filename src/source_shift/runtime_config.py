"""두 경로에 공통으로 적용하고 실행 기록에도 저장하는 설정."""

ENGINE_CONFIG = {
    "consume_fuel": True,
    "parallel_compilation": False,
    "cranelift_opt_level": "speed",
}
STORE_LIMITS = {
    "memory_size": 2097152,
    "table_elements": 1000,
    "instances": 1,
    "tables": 1,
    "memories": 1,
}
