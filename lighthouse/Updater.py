import json
import os

from twisted.internet import defer, reactor
from twisted.internet.task import LoopingCall
from jsonrpc.proxy import JSONRPCProxy
from lbrynet.conf import API_CONNECTION_STRING
from lbrynet.core.LBRYMetadata import Metadata, verify_name_characters
import logging.handlers

log = logging.getLogger()


class MetadataUpdater(object):
    def __init__(self):
        reactor.addSystemEventTrigger('before', 'shutdown', self.stop)
        self.api = JSONRPCProxy.from_url(API_CONNECTION_STRING)
        self.cache_file = os.path.join(os.path.expanduser("~/"), ".lighthouse_cache")
        self.claimtrie_updater = LoopingCall(self._update_claimtrie)

        if os.path.isfile(self.cache_file):
            log.info("Loading cache")
            f = open(self.cache_file, "r")
            r = json.loads(f.read())
            f.close()
            self.claimtrie, self.metadata, self.bad_uris= r['claimtrie'], r['metadata'], r['bad_uris']
        else:
            log.info("Rebuilding metadata cache")
            self.claimtrie = []
            self.metadata = {}
            self.bad_uris = []

    def _filter_claimtrie(self):
        claims = self.api.get_nametrie()
        r = []
        for claim in claims:
            if claim['txid'] not in self.bad_uris:
                try:
                    verify_name_characters(claim['name'])
                    r.append(claim)
                except:
                    self.bad_uris.append(claim['txid'])
                    log.info("Bad name for claim %s" % claim['txid'])
        return r

    def _update_claimtrie(self):
        claimtrie = self._filter_claimtrie()
        if claimtrie != self.claimtrie:
            for claim in claimtrie:
                if claim['name'] not in self.metadata:
                    self._update_metadata(claim)
                elif claim['txid'] != self.metadata[claim['name']]['txid']:
                    self._update_metadata(claim)

    def _save_metadata(self, claim, metadata):
        try:
            m = Metadata(metadata)
        except:
            return self._notify_bad_metadata(claim)
        log.info("Validated lbry://%s" % claim['name'])
        self.metadata[claim['name']] = m
        self.metadata[claim['name']]['txid'] = claim['txid']
        if claim not in self.claimtrie:
            self.claimtrie.append(claim)
        return self._cache_metadata()

    def _notify_bad_metadata(self, claim):
        log.info("Bad metadata: " + str(claim['name']))
        if claim['txid'] not in self.bad_uris:
            self.bad_uris.append(claim['txid'])
        return self._cache_metadata()

    def _update_metadata(self, claim):
        d = defer.succeed(None)
        d.addCallback(lambda _: self.api.resolve_name({'name': claim['name']}))
        d.addCallbacks(lambda metadata: self._save_metadata(claim, metadata),
                       lambda _: self._notify_bad_metadata(claim))
        return d

    def _cache_metadata(self):
        r = {'metadata': self.metadata, 'claimtrie': self.claimtrie, 'bad_uris': self.bad_uris}
        f = open(self.cache_file, "w")
        f.write(json.dumps(r))
        f.close()
        return defer.succeed(None)

    def start(self):
        log.info("Starting updater")
        self.claimtrie_updater.start(30)

    def stop(self):
        log.info("Stopping updater")
        if self.claimtrie_updater.running:
            self.claimtrie_updater.stop()