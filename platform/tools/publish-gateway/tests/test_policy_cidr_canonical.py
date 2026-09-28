"""CIDR 规范形式必须与 Python 版本无关（policyDigest 的前提）。

为什么要单独钉：`policyDigest` 是网关算一遍、平台用 Java 独立再算一遍然后逐字比对。
网关这边原先直接用 `str(ipaddress.ip_network(...))`，而**标准库的写法跨版本变过**——
IPv4-mapped 的 IPv6 地址在 Python 3.9 是 `::ffff:102:304`，3.11 起是 `::ffff:1.2.3.4`
（3.11 更贴近 RFC 5952 对这类地址的建议）。Java 侧 `CanonicalIpNetwork.format()` 永远
输出纯 hextet，于是网关一旦跑在 3.11 上，带这类规则的问卷就会因摘要不符而**发布失败**。

所以规范形式必须由我们自己定死，不能继承标准库的格式化行为。选纯 hextet 是因为 Java
那份独立实现、已记录的向量表、以及所有已发布问卷的摘要都是这个形式。
"""

import ipaddress
import unittest

from pubgw.policy.schema import canonical_cidr


class CanonicalFormIsVersionIndependentTest(unittest.TestCase):
    def test_ipv4_mapped_ipv6_uses_hextets_not_dotted_quad(self):
        # 这一条正是跨版本分歧点：标准库 3.9 与 3.11 给出不同答案。
        self.assertEqual("::ffff:102:304/128", canonical_cidr("::ffff:1.2.3.4/128"))

    def test_it_does_not_follow_whatever_the_standard_library_happens_to_print(self):
        """标准库怎么写不影响我们——这条在 3.9 上恒真，在 3.11 上曾经是红的。"""
        library = str(ipaddress.ip_network("::ffff:1.2.3.4/128", strict=False))
        self.assertEqual("::ffff:102:304/128", canonical_cidr("::ffff:1.2.3.4/128"),
                         "规范形式跟着标准库走了，跨版本就会不一致（库这次给的是 {}）".format(library))

    def test_ordinary_ipv6_is_compressed_at_the_longest_zero_run(self):
        self.assertEqual("2001:db8::8a2e:370:7334/128",
                         canonical_cidr("2001:0db8:0000:0000:0000:8a2e:0370:7334/128"))

    def test_ipv4_is_untouched(self):
        self.assertEqual("10.0.0.0/8", canonical_cidr("10.0.0.1/8"))

    def test_host_bits_are_masked_off(self):
        self.assertEqual("2001:db8::/32", canonical_cidr("2001:db8::1/32"))

    def test_a_bare_address_gets_a_full_prefix(self):
        self.assertEqual("10.1.2.3/32", canonical_cidr("10.1.2.3"))
        self.assertEqual("::1/128", canonical_cidr("::1"))

    def test_rubbish_still_raises_so_the_caller_can_report_it(self):
        with self.assertRaises(ValueError):
            canonical_cidr("not-an-address")
