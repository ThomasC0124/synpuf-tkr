import os
import json
import ntpath
import logging
import subprocess

from parser import Parser
from resource import (
    inpatient_claim_header,
    outpatient_claim_header,
    carrier_claim_header,
    pde_header
)

SCRIPT_DIR = os.path.dirname(__file__)

class ClaimParser(Parser):
    _type_header_map = {
        'inpatient': inpatient_claim_header,
        'outpatient': outpatient_claim_header,
        'carrier': carrier_claim_header,
        'pde': pde_header
    }
    _claim_line_sorting_unix_script = os.path.join(SCRIPT_DIR, 'unix_sort.sh')

    def __init__(self, parser_type):
        super(ClaimParser, self).__init__()
        self.logger = logging.getLogger('ClaimParser')
        assert parser_type in self._type_header_map, self.logger.error(
            'parser type {} not available'.format(parser_type)
        )
        self._parser_type = parser_type
        self._load_header()
        assert self._ref_header is not None, self.logger.error('reference header not loaded')

    def __repr__(self):
        desc = '<Parser - {} claim parser>'.format(self._parser_type)
        return desc

    def _load_header(self):
        """Load JSON-like reference header"""
        self._ref_header = self._type_header_map[self._parser_type]

    def merge_claim_lines(self):
        """Merge *ALL* claim lines in `self._file_queue`"""
        temp_fn_claim_lines_to_sort = './temp_claim_lines_to_sort.txt'
        with open(temp_fn_claim_lines_to_sort, 'w') as fp_out:
            while len(self._file_queue) > 0:
                next_data_file = self.pop_data_file()
                self._open_data_file(next_data_file)
                if self._data_handle is None:
                    self.logger.warning(
                        'skipping the claim line file {} since it can\'t be opened'.format(
                            next_data_file
                        )
                    )
                    continue
                for claim_line in self._data_handle:
                    claim_line = json.loads(claim_line)
                    if self._is_data_complete(claim_line) is False:
                        continue
                    member_id = claim_line.pop('memberID')
                    fp_out.write('{}|{}\n'.format(member_id, json.dumps(claim_line)))
                self._close_data_file()
                os.remove(next_data_file)
        temp_fn_claim_lines_sorted = './temp_claim_lines_sorted.txt'
        subprocess.call(
            [self._claim_line_sorting_unix_script, temp_fn_claim_lines_to_sort,
             temp_fn_claim_lines_sorted]
        )
        os.remove(temp_fn_claim_lines_to_sort)
        merged_claims = self._merge_claim_lines_by_claim_id(temp_fn_claim_lines_sorted)
        os.remove(temp_fn_claim_lines_sorted)
        return merged_claims

    def _is_data_complete(self, claim_line):
        """Determine whether `claim_line` is complete"""
        for k in ['memberID', 'startDate']:
            if k not in claim_line:
                return False
        if 'claimID' not in claim_line and 'fillID' not in claim_line:
            return False
        return True

    def _merge_claim_lines_by_claim_id(self, claim_lines_sorted_by_member_id):
        """Merge claim lines already sorted by member ID by claim ID"""
        merged_claims = {}
        with open(claim_lines_sorted_by_member_id, 'r') as fp_in:
            last_member_id = None
            claim_line_container = []
            for line in fp_in:
                member_id, claim_line = line.strip().split('|')
                claim_line = json.loads(claim_line)
                if self._to_dump_container(member_id, last_member_id):
                    claims = self._clean_up_container(claim_line_container)
                    merged_claims[last_member_id] = claims
                    claim_line_container = []
                claim_line_container.append(claim_line)
                last_member_id = member_id
        if len(claim_line_container) > 0:
            claims = self._clean_up_container(claim_line_container)
            merged_claims[last_member_id] = claims
        return merged_claims

    def _to_dump_container(self, current_mid, last_mid):
        """Determine whether to dump collected claim lines for further processing"""
        to_dump = False
        if current_mid != last_mid and last_mid is not None:
            to_dump = True
        return to_dump

    def _clean_up_container(self, claim_line_container):
        """Merge claim lines by claim ID"""
        claims = {}
        for claim_line in claim_line_container:
            if 'claimLine' in claim_line:
                del claim_line['claimLine']
            claim_id = claim_line.get('claimID', 'NA')  # either claimID or fillID
            if claim_id == 'NA':
                claim_id = claim_line['fillID']
            if claim_id in claims:
                existing_claim = claims[claim_id]
                for k, v in claim_line.iteritems():
                    if k in existing_claim:
                        if isinstance(v, list):
                            existing_claim[k] = list(set(existing_claim[k]+v))
                        else:
                            if v != existing_claim[k]:
                                self.logger.info(
                                    'updating claim ({}) - {} field from "{}" to "{}"'.format(
                                        claim_id, k, existing_claim[k], v
                                    )
                                )
                                existing_claim[k] = v
                    else:
                        existing_claim[k] = v
            else:
                claims[claim_id] = claim_line
        claim_list = []
        for _, claim in claims.iteritems():
            claim['claimType'] = self._parser_type
            claim_list.append(claim)
        claims = sorted(claim_list, key=lambda claim: claim['startDate'])
        return claims
