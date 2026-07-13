from time import sleep

from utils.i18n import t
from utils.tools import get_logger
import utils.constants as constants

max_retries = 2

logger = get_logger(constants.log_path)


def retry_func(func, retries=max_retries, name=""):
    """
    Retry the function
    """
    for i in range(retries):
        try:
            sleep(1)
            return func()
        except Exception as e:
            if name and i < retries - 1:
                logger.warning(t("msg.failed_retrying_count").format(name=name, count=i + 1))
            elif i == retries - 1:
                raise Exception(
                    t("msg.failed_retry_max").format(name=name)
                )
    raise Exception(t("msg.failed_retry_max").format(name=name))
