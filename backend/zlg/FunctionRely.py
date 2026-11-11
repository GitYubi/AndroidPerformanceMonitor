import inspect
import traceback

from zlg.logutil import Logs

class FunctionRely():
    log = Logs()

    @classmethod
    def try_func_comments(cls, content:str):
        def try_func(func):
            """
            异常信息日志。
            param： func: 要装饰的函数
            return：wrapper
            """
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except:
                    cls.log.error(f"\"{func.__name__}\"方法异常，异常信息如下：{content}"
                        f"\n{traceback.format_exc()}")
            return wrapper
        return try_func

    @classmethod
    def var_name_get(cls, content: dict,var: object)-> str:
        """
        # 查询字典的键，根据值
        param： content: 字典
        param： var：字典的值
        return：字典的键
        """
        for k, v in content.items():
            if content[k] == var:
                return k
        else:
            return 0

if __name__ == '__main__':
    @FunctionRely.try_func_comments("测试注释")
    def raise_func():
        raise 
    
    raise_func()