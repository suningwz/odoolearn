# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from __future__ import print_function
import math

from odoo.tools import pycompat

if not pycompat.PY2:
    import builtins
    def round(f):
        # P3's builtin round differs from P2 in the following manner:
        # * it rounds half to even rather than up (away from 0)
        # * round(-0.) loses the sign (it returns -0 rather than 0)
        # * round(x) returns an int rather than a float
        #
        # this compatibility shim implements Python 2's round in terms of
        # Python 3's so that important rounding error under P3 can be
        # trivially fixed, assuming the P2 behaviour to be debugged and
        # correct.
        roundf = builtins.round(f)
        if builtins.round(f + 1) - roundf != 1:
            return f + math.copysign(0.5, f)
        # copysign ensures round(-0.) -> -0 *and* result is a float
        return math.copysign(roundf, f)
else:
    round = round

def _float_check_precision(precision_digits=None, precision_rounding=None):
    #assert格式： assert+空格+判断语句+双引号"报错语句"，判断语句为True时，什么也不输出，为False时，输出后面引号的报错语句
    # 这里，precision_digits和precision_rounding必须指定一个值，assert判断语句才为True，这2个参数不能都指定，也不能都不指定
    assert (precision_digits is not None or precision_rounding is not None) and \   
        not (precision_digits and precision_rounding),\
         "exactly one of precision_digits and precision_rounding must be specified"    
    if precision_digits is not None:
        return 10 ** -precision_digits  #10的负幂次方，例如precision_digits为2时，表示小数精确度为2，这里返回10的负二次幂，即0.01
    return precision_rounding   #recision_rounding是个什么样的值？参数precision_rounding 浮点类型：十进制小数表示期望精度的最小非零值，例如，2位精度为0.01

#precision_digits精度数，即小数位数，比如2，即小数精度为2；precision_rounding舍入进度，例如0.01
def float_round(value, precision_digits=None, precision_rounding=None, rounding_method='HALF-UP'): 
    """Return ``value`` rounded to ``precision_digits`` decimal digits,          将value值舍入，返回到precision_digits
       minimizing IEEE-754 floating point representation errors, and applying    最小化IEEE-754标准浮点表示的误差，
       the tie-breaking rule selected with ``rounding_method``, by default       应用舍入方法（即打破平局的规则），
       HALF-UP (away from zero).                                                 默认为HALF-UP(half表示半，即四舍五入)
       Precision must be given by ``precision_digits`` or ``precision_rounding``, 
       not both!                                                                 精度由precision_digits或precision_rounding决定，但不能同时提供这两者                

       :param float value: the value to round                                    参数value 浮点数类型：要四舍五入的值
       :param int precision_digits: number of fractional digits to round to.     参数precision_digits 整数类型：需要四舍五入的小数位位数
       :param float precision_rounding: decimal number representing the minimum  参数precision_rounding 浮点类型：十进制小数表示期望精度的最小非零值
           non-zero value at the desired precision (for example, 0.01 for a      例如，2位精度为0.01
           2-digit precision).
       :param rounding_method: the rounding method used: 'HALF-UP', 'UP' or 'DOWN',  参数 rouding_method：用于舍入的方法有：'HALF-UP','UP','DOWN'
           the first one rounding up to the closest number with the rule that        第一个（HALF-UP）表示根据规则舍入到最接近的数字，
           number>=0.5 is rounded up to 1, the second always rounding up and the     规则是数字大于等于0.5，舍入到1，第二个（UP）总是往上入
           latest one always rounding down.                                          第二个（DOWN）,总是往下舍
       :return: rounded float                 #返回：经过舍入的浮点数

       IEEE浮点数算术标准（IEEE 754）是最广泛使用的浮点数运算标准，为许多CPU与浮点运算器所采用。
       IEEE 754规定了四种表示浮点数值的方式：单精确度（32位）、双精确度（64位）、延伸单精确度（43位以上，很少使用）与延伸双精确度
    """
    rounding_factor = _float_check_precision(precision_digits=precision_digits,
                                             precision_rounding=precision_rounding)
    if rounding_factor == 0 or value == 0: return 0.0

    # NORMALIZE - ROUND - DENORMALIZE
    # In order to easily support rounding to arbitrary 'steps' (e.g. coin values),
    # we normalize the value before rounding it as an integer, and de-normalize
    # after rounding: e.g. float_round(1.3, precision_rounding=.5) == 1.5
    # Due to IEE754 float/double representation limits, the approximation of the 由于IEE754 单/双进度表示的限制，实际值的近似值可能略低于
    # real value may be slightly below the tie limit, resulting in an error of   近似极限值，导致四舍五入后最后一个位置(ulp)的误差为1个单位
    # 1 unit in the last place (ulp) after rounding.
    # For example 2.675 == 2.6749999999999998.   #这个等式的结果为True
      #比较两个浮点数是否相等一般是用 if(abs(f1-f2)<epsilon)来判断，一般epsilon都取1E-6左右。
    # To correct this, we add a very small epsilon value, scaled to the   #要纠正这一点，我们增加一个非常小的epsilon值  
    # the order of magnitude of the value, to tip the tie-break in the right  #按数值的数量级进行缩放
    # direction.
    # Credit: discussion with OpenERP community members on bug 882036

    normalized_value = value / rounding_factor # normalize    #根据舍入精度，将小数点右移
    sign = math.copysign(1.0, normalized_value)  #若normalized_value<0,返回-1.0，否则返回1.0
    epsilon_magnitude = math.log(abs(normalized_value), 2)  #返回abs(mormalized)的以2位底数的对数
    epsilon = 2**(epsilon_magnitude-53)  #这个值有什么特点和意义？=(2**epsilon_magnitude)*(2**-53); 2**-53是双进度浮点数中有效数的最小精度，(2**epsilon_magnitude)*(2**-53)则是normalized_value的最小精度

    # TIE-BREAKING: UP/DOWN (for ceiling[resp. flooring] operations)
    # When rounding the value up[resp. down], we instead subtract[resp. add] the epsilon value
    # as the the approximation of the real value may be slightly *above* the
    # tie limit, this would result in incorrectly rounding up[resp. down] to the next number
    # The math.ceil[resp. math.floor] operation is applied on the absolute value in order to
    # round "away from zero" and not "towards infinity", then the sign is
    # restored.

    if rounding_method == 'UP':
        normalized_value -= sign*epsilon  #normalied_value=normalized_value-sign*epsilon
        rounded_value = math.ceil(abs(normalized_value)) * sign   #ceil()返回数字的上入整数，例如math.ceil(-5.1),返回-5，math.ceil(5.1)返回6

    elif rounding_method == 'DOWN':
        normalized_value += sign*epsilon
        rounded_value = math.floor(abs(normalized_value)) * sign

    # TIE-BREAKING: HALF-UP (for normal rounding)
    # We want to apply HALF-UP tie-breaking rules, i.e. 0.5 rounds away from 0.
    else:
        normalized_value += math.copysign(epsilon, normalized_value)
        rounded_value = round(normalized_value)     # round to integer

    result = rounded_value * rounding_factor # de-normalize
    return result

def float_is_zero(value, precision_digits=None, precision_rounding=None): #当value根据精度参数四舍五入后的值小于precision_rounding时，认为value值为0
    """Returns true if ``value`` is small enough to be treated as
       zero at the given precision (smaller than the corresponding *epsilon*).
       The precision (``10**-precision_digits`` or ``precision_rounding``)
       is used as the zero *epsilon*: values less than that are considered
       to be zero.
       Precision must be given by ``precision_digits`` or ``precision_rounding``,
       not both! 

       Warning: ``float_is_zero(value1-value2)`` is not equivalent to
       ``float_compare(value1,value2) == 0``, as the former will round after
       computing the difference, while the latter will round before, giving
       different results for e.g. 0.006 and 0.002 at 2 digits precision. 

       :param int precision_digits: number of fractional digits to round to.
       :param float precision_rounding: decimal number representing the minimum
           non-zero value at the desired precision (for example, 0.01 for a 
           2-digit precision).
       :param float value: value to compare with the precision's zero
       :return: True if ``value`` is considered zero
    """
    epsilon = _float_check_precision(precision_digits=precision_digits,
                                             precision_rounding=precision_rounding)
    return abs(float_round(value, precision_rounding=epsilon)) < epsilon

def float_compare(value1, value2, precision_digits=None, precision_rounding=None):
    """Compare ``value1`` and ``value2`` after rounding them according to the
       given precision. A value is considered lower/greater than another value
       if their rounded value is different. This is not the same as having a
       non-zero difference!
       Precision must be given by ``precision_digits`` or ``precision_rounding``,
       not both!

       Example: 1.432 and 1.431 are equal at 2 digits precision,
       so this method would return 0
       However 0.006 and 0.002 are considered different (this method returns 1)
       because they respectively round to 0.01 and 0.0, even though
       0.006-0.002 = 0.004 which would be considered zero at 2 digits precision.

       Warning: ``float_is_zero(value1-value2)`` is not equivalent to 
       ``float_compare(value1,value2) == 0``, as the former will round after
       computing the difference, while the latter will round before, giving
       different results for e.g. 0.006 and 0.002 at 2 digits precision. 

       :param int precision_digits: number of fractional digits to round to.
       :param float precision_rounding: decimal number representing the minimum
           non-zero value at the desired precision (for example, 0.01 for a 
           2-digit precision).
       :param float value1: first value to compare
       :param float value2: second value to compare
       :return: (resp.) -1, 0 or 1, if ``value1`` is (resp.) lower than,
           equal to, or greater than ``value2``, at the given precision.
    """
    rounding_factor = _float_check_precision(precision_digits=precision_digits,
                                             precision_rounding=precision_rounding)
    value1 = float_round(value1, precision_rounding=rounding_factor)
    value2 = float_round(value2, precision_rounding=rounding_factor)
    delta = value1 - value2
    if float_is_zero(delta, precision_rounding=rounding_factor): return 0
    return -1 if delta < 0.0 else 1

def float_repr(value, precision_digits):
    """Returns a string representation of a float with the
       the given number of fractional digits. This should not be
       used to perform a rounding operation (this is done via
       :meth:`~.float_round`), but only to produce a suitable
       string representation for a float.

        :param int precision_digits: number of fractional digits to
                                     include in the output
    """
    # Can't use str() here because it seems to have an intrisic
    # rounding to 12 significant digits, which causes a loss of
    # precision. e.g. str(123456789.1234) == str(123456789.123)!!
    return ("%%.%sf" % precision_digits) % value

_float_repr = float_repr

def float_split_str(value, precision_digits):
    """ Splits the given float 'value' in its unitary and decimal parts. The value
       is first rounded thanks to the ``precision_digits`` argument given.

       Example: 1.432 would return (1, 43) for a digits precision of 2.

       :param float value: value to split.
       :param int precision_digits: number of fractional digits to round to.
       :return: returns the tuple(<unitary part>, <decimal part>) of the given value
       :rtype: tuple(str, str)
    """
    value = float_round(value, precision_digits=precision_digits)
    value_repr = float_repr(value, precision_digits)
    units, cents = value_repr.split('.')
    return units, cents

def float_split(value, precision_digits):
    """ same as float_split_str() except that it returns the unitary and decimal
        parts as integers instead of strings.

       :rtype: tuple(int, int)
    """
    units, cents = float_split_str(value, precision_digits)
    return int(units), int(cents)


if __name__ == "__main__":

    import time
    start = time.time()
    count = 0
    errors = 0

    def try_round(amount, expected, precision_digits=3):
        global count, errors; count += 1
        result = float_repr(float_round(amount, precision_digits=precision_digits),
                            precision_digits=precision_digits)
        if result != expected:
            errors += 1
            print('###!!! Rounding error: got %s , expected %s' % (result, expected))

    # Extended float range test, inspired by Cloves Almeida's test on bug #882036.
    fractions = [.0, .015, .01499, .675, .67499, .4555, .4555, .45555]
    expecteds = ['.00', '.02', '.01', '.68', '.67', '.46', '.456', '.4556']
    precisions = [2, 2, 2, 2, 2, 2, 3, 4]
    for magnitude in range(7):
        for frac, exp, prec in pycompat.izip(fractions, expecteds, precisions):
            for sign in [-1,1]:
                for x in range(0, 10000, 97):
                    n = x * 10**magnitude
                    f = sign * (n + frac)
                    f_exp = ('-' if f != 0 and sign == -1 else '') + str(n) + exp
                    try_round(f, f_exp, precision_digits=prec)

    stop = time.time()

    # Micro-bench results:
    # 47130 round calls in 0.422306060791 secs, with Python 2.6.7 on Core i3 x64
    # with decimal:
    # 47130 round calls in 6.612248100021 secs, with Python 2.6.7 on Core i3 x64
    print(count, " round calls, ", errors, "errors, done in ", (stop-start), 'secs')
