"""Publication of a style capture into the fixed archive."""

import uuid
from webcapture.archive.evidence import EvidenceStore
from webcapture.archive.schema import IntegrityError
from webcapture.archive.style_manifests import (StyleManifest, StyleManifestStore,
                                                StyleReferenceRecord, StyleResourceRecord)
from webcapture.capture.jobs import CandidateState
from webcapture.capture.style_jobs import check_style_candidate_complete


class StylePublicationStore:
    def __init__(self, archive):
        self.archive = archive

    def publish(self, candidate) -> str:
        if candidate.state != CandidateState.COMPLETE:
            raise IntegrityError("only complete candidates can be published")
        check_style_candidate_complete(candidate)
        graph = candidate.graph
        capture_id = uuid.uuid4().hex
        with self.archive.transaction():
            evidence = EvidenceStore(self.archive)
            identities = {request_url: evidence.stage(capture_id, response)
                          for request_url, response in graph.responses.items()}
            resources = tuple(
                StyleResourceRecord(request_url, identities[request_url],
                                    graph.roles_for(request_url))
                for request_url in graph.order)
            references = tuple(
                StyleReferenceRecord(**record)
                for document in graph.documents
                for record in graph.references[document])
            manifest = StyleManifest(identities[candidate.entry_url], resources,
                                     tuple(graph.documents), references)
            digest = StyleManifestStore(self.archive).save(capture_id, manifest)
            self.archive.connection.execute(
                "INSERT INTO publications(capture_id,manifest_digest) VALUES(?,?)",
                (capture_id, digest))
        return capture_id
