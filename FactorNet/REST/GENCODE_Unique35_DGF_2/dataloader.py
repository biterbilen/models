"""Basenji dataloader
"""
# python2, 3 compatibility
from __future__ import absolute_import, division, print_function

import os
import numpy as np
import pandas as pd
import pybedtools
from pybedtools import BedTool
from genomelake.extractors import FastaExtractor, BigwigExtractor
from kipoi.data import Dataset
from kipoi.metadata import GenomicRanges
import linecache
from six.moves.urllib.request import urlretrieve

# Get the local path
import inspect

filename = inspect.getframeinfo(inspect.currentframe()).filename
this_dir = os.path.dirname(os.path.abspath(filename))
# --------------------------------------------


class BedToolLinecache(BedTool):
    """Faster BedTool accessor by Ziga Avsec
    Normal BedTools loops through the whole file to get the
    line of interest. Hence the access it o(n)
    Note: this might load the whole bedfile into memory
    """

    def __getitem__(self, idx):
        line = linecache.getline(self.fn, idx + 1)
        return pybedtools.create_interval_from_list(line.strip().split("\t"))


class SeqDataset(Dataset):
    """
    Args:
        intervals_file: bed3 file containing intervals
        fasta_file: file path; Genome sequence
        target_file: file path; path to the targets in the csv format
    """

    SEQ_WIDTH = 1002

    def __init__(self,
                 intervals_file,
                 fasta_file,
                 dnase_file,
                 cell_line=None,
                 mappability_file=None,
                 GENCODE_dir=None,
                 use_linecache=True):

        # intervals
        if use_linecache:
            linecache.clearcache()
            BT = BedToolLinecache
        else:
            BT = BedTool

        self.bt = BT(intervals_file)

        # Fasta
        self.fasta_extractor = FastaExtractor(fasta_file)

        # DNase
        self.dnase_extractor = BigwigExtractor(dnase_file)
        # mappability
        if mappability_file is None:
        # download the mappability file if not existing
            mappability_file = os.path.join(this_dir, "../../template/dataloader_files",
          "wgEncodeDukeMapabilityUniqueness35bp.bigWig")
            if not os.path.exists(mappability_file):
                print("Downloading the mappability file")
                urlretrieve("http://hgdownload.cse.ucsc.edu/goldenPath/hg19/encodeDCC/wgEncodeMapability/wgEncodeDukeMapabilityUniqueness35bp.bigWig", mappability_file)
                print("Download complete")

            
            
            
        self.mappability_extractor = BigwigExtractor(mappability_file)
        # Gencode features
        if GENCODE_dir is None:
            gp = os.path.join(this_dir, "dataloader_files/gencode_features/")
        else:
            gp = GENCODE_dir
        self.gencode_beds = [
            ("cpg", BedTool(gp + '/cpgisland.bed.gz')),
            ("cds", BedTool(gp + '/wgEncodeGencodeBasicV19.cds.merged.bed.gz')),
            ("intron", BedTool(gp + '/wgEncodeGencodeBasicV19.intron.merged.bed.gz')),
            ("promoter", BedTool(gp + '/wgEncodeGencodeBasicV19.promoter.merged.bed.gz')),
            ("utr5", BedTool(gp + '/wgEncodeGencodeBasicV19.utr5.merged.bed.gz')),
            ("utr3", BedTool(gp + '/wgEncodeGencodeBasicV19.utr3.merged.bed.gz')),
        ]
        # Overlap beds - could be done incrementally
        print("Overlapping all the bed-files")
        # The BT() and .fn are there in order to leverage BedToolLinecache
        self.overlap_beds = [(b, BT(self.bt.intersect(v, wa=True, c=True).fn))
                             for b, v in self.gencode_beds]
        print("Assesing the file")
        assert len(self.overlap_beds[1][1]) == len(self.bt)

    def __len__(self):
        return len(self.bt)

    def __getitem__(self, idx):
        # Get the interval
        interval = self.bt[idx]
        if interval.stop - interval.start != self.SEQ_WIDTH:
            center = (interval.start + interval.stop) // 2
            interval.start = center - self.SEQ_WIDTH // 2
            interval.end = center + self.SEQ_WIDTH // 2 + self.SEQ_WIDTH % 2
        # Get the gencode features
        gencode_counts = np.array([v[idx].count for k, v in self.overlap_beds],
                                  dtype=bool)

        # Run the fasta extractor
        seq = np.squeeze(self.fasta_extractor([interval]), axis=0)
        seq_rc = seq[::-1, ::-1]

        # Dnase
        dnase = np.squeeze(self.dnase_extractor([interval], axis=0))[:, np.newaxis]
        dnase[np.isnan(dnase)] = 0  # NA fill
        dnase_rc = dnase[::-1]

        bigwig_list = [seq]
        bigwig_rc_list = [seq_rc]
        mappability = np.squeeze(self.mappability_extractor([interval], axis=0))[:, np.newaxis]
        mappability[np.isnan(mappability)] = 0  # NA fill
        mappability_rc = mappability[::-1]
        bigwig_list.append(mappability)
        bigwig_rc_list.append(mappability_rc)
        bigwig_list.append(dnase)
        bigwig_rc_list.append(dnase_rc)

        ranges = GenomicRanges.from_interval(interval)
        ranges_rc = GenomicRanges.from_interval(interval)
        ranges_rc.strand = "-"

        return {
            "inputs": [
                np.concatenate(bigwig_list, axis=-1),  # stack along the last axis
                np.concatenate(bigwig_rc_list, axis=-1),  # RC version
                gencode_counts
            ],
            "targets": {},  # No Targets
            "metadata": {
                "ranges": ranges,
                "ranges_rc": ranges_rc
            }
        }