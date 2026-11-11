from zlg.paths import data_path
import logging
import logging.handlers
import os
import time


class Logs(logging.Logger):

    def __init__(self,
                 name='root',
                 logger_level= 'INFO',
                 file= "",
                 logger_format = "[%(process)s]%(asctime)s [%(levelname)s] "
                                 "[%(filename)s %(funcName)s] "
                                 "[line:%(lineno)d] %(message)s"
                 ):
        # 1、设置logger收集器，继承logging.Logger
        super().__init__(name)


        # 2、设置日志收集器level级别
        self.setLevel(logger_level)
        # DEBUG

        # 5、设置 handler 格式
        fmt = logging.Formatter(logger_format)

        # 3、设置日志处理器

        # 创建文件目录
        logs_dir = data_path("repeat_logs")
        # 修改log保存位置
        timestamp = time.strftime("%Y-%m-%d", time.localtime())
        logfilename = '%s.txt' % timestamp
        logfilepath = os.path.join(logs_dir, logfilename)
        # 如果传递了文件，就会输出到file文件中
        if not file:
            file = logfilepath
        file_handler = logging.FileHandler(file, encoding="utf-8", delay=True)
        # 4、设置 file_handler 级别
        file_handler.setLevel(logger_level)
        # 6、设置handler格式
        file_handler.setFormatter(fmt)
        # 7、添加handler
        self.addHandler(file_handler)

    def close(self):
        for handler in self.handlers[:]:
            self.removeHandler(handler)
            handler.close()


if __name__ == '__main__':
   Logs().debug("debug")
   Logs().info('info')
   Logs().error('error')
