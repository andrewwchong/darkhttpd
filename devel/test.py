#!/usr/bin/env python
import unittest
import socket
import signal
import re
import os
import random

WWWROOT = "tmp.httpd.tests"

class Conn:
    def __init__(self):
        self.port = 12346
        self.s = socket.socket()
        self.s.connect(("0.0.0.0", self.port))
        # connect throws socket.error on connection refused

    def get(self, url, http_ver="1.0", endl="\n", req_hdrs={}, method="GET"):
        req = method+" "+url
        if http_ver is not None:
            req += " HTTP/"+http_ver
        req += endl
        if http_ver is not None:
            req_hdrs["User-Agent"] = "test.py"
            req_hdrs["Connection"] = "close"
            for k,v in req_hdrs.items():
                req += k+": "+v+endl
        req += endl # end of request
        self.s.send(req)
        ret = ""
        while True:
            signal.alarm(1) # don't wait forever
            r = self.s.recv(65536)
            signal.alarm(0)
            if r == "":
                break
            else:
                ret += r
        return ret

def parse(resp):
    """
    Parse response into status line, headers and body.
    """
    pos = resp.index("\r\n\r\n") # throws exception on failure
    head = resp[:pos]
    body = resp[pos+4:]
    status,head = head.split("\r\n", 1)
    hdrs = {}
    for line in head.split("\r\n"):
        k, v = line.split(": ", 1)
        hdrs[k] = v
    return (status, hdrs, body)

class TestHelper(unittest.TestCase):
    def assertContains(self, body, *strings):
        for s in strings:
            self.assertTrue(s in body,
                            msg="expected %s in %s"%(repr(s), repr(body)))

    def assertIsIndex(self, body, path):
        self.assertContains(body,
            "<title>%s</title>\n"%path,
            "<h1>%s</h1>\n"%path,
            '<a href="..">..</a>/',
            'Generated by darkhttpd')

    def assertIsInvalid(self, body, path):
        self.assertContains(body,
            "<title>400 Bad Request</title>",
            "<h1>Bad Request</h1>\n",
            "You requested an invalid URL: %s\n"%path,
            'Generated by darkhttpd')

class TestDirList(TestHelper):
    def setUp(self):
        self.fn = WWWROOT+"/escape#this"
        open(self.fn, "w").write("x"*12345)

    def tearDown(self):
        os.unlink(self.fn)

    def test_dirlist_escape(self):
        resp = Conn().get("/")
        status, hdrs, body = parse(resp)
        self.assertEquals(ord("#"), 0x23)
        self.assertContains(body, "escape%23this", "12345")

class TestCases(TestHelper):
    pass # these get autogenerated in setUpModule()

def nerf(s):
    return re.sub("[^a-zA-Z0-9]", "_", s)

def makeCase(name, url, hdr_checker=None, body_checker=None,
             req_hdrs={"User-Agent": "test.py"},
             http_ver=None, endl="\n"):
    def do_test(self):
        resp = Conn().get(url, http_ver, endl, req_hdrs)
        if http_ver is None:
            status = ""
            hdrs = {}
            body = resp
        else:
            status, hdrs, body = parse(resp)

        if hdr_checker is not None and http_ver is not None:
            hdr_checker(self, hdrs)

        if body_checker is not None:
            body_checker(self, body)

        # FIXME: check status
        if http_ver is not None:
            prefix = "HTTP/1.1 " # should 1.0 stay 1.0?
            self.assertTrue(status.startswith(prefix),
                msg="%s at start of %s"%(repr(prefix), repr(status)))

    v = http_ver
    if v is None:
        v = "0.9"
    test_name = "_".join([
        "test",
        nerf(name),
        nerf("HTTP"+v),
        {"\n":"LF", "\r\n":"CRLF"}[endl],
    ])
    do_test.__name__ = test_name # hax
    setattr(TestCases, test_name, do_test)

def makeCases(name, url, hdr_checker=None, body_checker=None,
              req_hdrs={"User-Agent": "test.py"}):
    for http_ver in [None, "1.0", "1.1"]:
        for endl in ["\n", "\r\n"]:
            makeCase(name, url, hdr_checker, body_checker,
                     req_hdrs, http_ver, endl)

def makeSimpleCases(name, url, assert_name):
    makeCases(name, url, None,
        lambda self,body: getattr(self, assert_name)(body, url))

def setUpModule():
    for args in [
        ["index",                "/",               "assertIsIndex"],
        ["up dir",               "/dir/../",        "assertIsIndex"],
        ["extra slashes",        "//dir///..////",  "assertIsIndex"],
        ["no trailing slash",    "/dir/..",         "assertIsIndex"],
        ["no leading slash",     "dir/../",         "assertIsInvalid"],
        ["invalid up dir",       "/../",            "assertIsInvalid"],
        ["fancy invalid up dir", "/./dir/./../../", "assertIsInvalid"],
        ]:
        makeSimpleCases(*args)

class TestFileGet(TestHelper):
    def setUp(self):
        self.datalen = 2345
        self.data = "".join(
            [chr(random.randint(0,255)) for _ in xrange(self.datalen)])
        self.url = "/data.jpeg"
        self.fn = WWWROOT + self.url
        open(self.fn, "w").write(self.data)

    def tearDown(self):
        os.unlink(self.fn)

    def test_file_get(self):
        resp = Conn().get(self.url)
        status, hdrs, body = parse(resp)
        self.assertContains(status, "200 OK")
        self.assertEquals(hdrs["Accept-Ranges"], "bytes")
        self.assertEquals(hdrs["Content-Length"], str(self.datalen))
        self.assertEquals(hdrs["Content-Type"], "image/jpeg")
        self.assertEquals(body, self.data)

    def test_file_head(self):
        resp = Conn().get(self.url, method="HEAD")
        status, hdrs, body = parse(resp)
        self.assertContains(status, "200 OK")
        self.assertEquals(hdrs["Accept-Ranges"], "bytes")
        self.assertEquals(hdrs["Content-Length"], str(self.datalen))
        self.assertEquals(hdrs["Content-Type"], "image/jpeg")

    def test_if_modified_since(self):
        resp1 = Conn().get(self.url, method="HEAD")
        status, hdrs, body = parse(resp1)
        lastmod = hdrs["Last-Modified"]

        resp2 = Conn().get(self.url, method="GET", req_hdrs =
            {"If-Modified-Since": lastmod })
        status, hdrs, body = parse(resp2)
        self.assertContains(status, "304 Not Modified")
        self.assertEquals(hdrs["Accept-Ranges"], "bytes")
        self.assertFalse(hdrs.has_key("Last-Modified"))
        self.assertFalse(hdrs.has_key("Content-Length"))
        self.assertFalse(hdrs.has_key("Content-Type"))

    def drive_range(self, range_in, range_out, len_out, data_out,
            status_out = "206 Partial Content"):
        resp = Conn().get(self.url, req_hdrs = {"Range": "bytes="+range_in})
        status, hdrs, body = parse(resp)
        self.assertContains(status, status_out)
        self.assertEquals(hdrs["Accept-Ranges"], "bytes")
        self.assertEquals(hdrs["Content-Range"], "bytes "+range_out)
        self.assertEquals(hdrs["Content-Length"], str(len_out))
        self.assertEquals(body, data_out)

    def test_range_reasonable(self):
        self.drive_range("10-20", "10-20/%d" % self.datalen,
            20-10+1, self.data[10:20+1])

    def test_range_tail(self):
        self.drive_range("10-", "10-%d/%d" % (self.datalen-1, self.datalen),
            self.datalen-10, self.data[10:])

    def test_range_negative(self):
        self.drive_range("-25", "%d-%d/%d" % (
            self.datalen-25, self.datalen-1, self.datalen),
            25, self.data[-25:])

    def test_range_bad_end(self):
        # expecting same result as test_range_negative
        self.drive_range("%d-%d"%(self.datalen-25, self.datalen*2),
            "%d-%d/%d"%(self.datalen-25, self.datalen-1, self.datalen),
            25, self.data[-25:])

    def test_range_bad_start(self):
        resp = Conn().get(self.url, req_hdrs = {"Range": "bytes=%d-"%(
            self.datalen*2)})
        status, hdrs, body = parse(resp)
        self.assertContains(status, "416 Requested Range Not Satisfiable")

if __name__ == '__main__':
    setUpModule()
    unittest.main()

# vim:set ts=4 sw=4 et:
