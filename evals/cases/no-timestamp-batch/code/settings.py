"""读取 report-job 配置：文件里的值 + 环境变量覆盖。"""

import os
import tomllib

ENV_OVERRIDES = {
    "output_dir": "REPORT_JOB_OUTPUT_DIR",
    "format": "REPORT_JOB_FORMAT",
    "region": "REPORT_JOB_REGION",
}


def load(path: str) -> dict:
    with open(path, "rb") as fh:
        settings = tomllib.load(fh)
    for key, env in ENV_OVERRIDES.items():
        if env in os.environ:
            settings[key] = os.environ[env]
        elif os.environ.get(env.replace("REPORT_JOB_", "RJ_")):
            # 兼容旧变量名
            settings[key] = os.environ[env.replace("REPORT_JOB_", "RJ_")]
    print(f"settings: region source = {ENV_OVERRIDES['region']} (env)")
    return settings
