# -*- coding: utf-8 -*-
from __future__ import print_function
import sys
import os
import logging
import luigi
from joblib import Parallel, delayed
import numpy as np

from pyrate.tasks.utils import pythonify_config
from pyrate.tasks.prepifg import PrepareInterferograms
from pyrate import prepifg
from pyrate import config as cf
from pyrate import roipac
from pyrate import gamma
from pyrate.shared import write_geotiff, mkdir_p
from pyrate.tasks import gamma as gamma_task
import pyrate.ifgconstants as ifc
from pyrate import mpiops

log = logging.getLogger(__name__)

ROI_PAC_HEADER_FILE_EXT = 'rsc'
GAMMA = 1
ROIPAC = 0


def main(params=None):
    """
    :param params: parameters dictionary read in from the config file
    :return:
    TODO: looks like base_igf_paths are ordered according to ifg list
    Sarah says this probably won't be a problem because they only removedata
    from ifg list to get pyrate to work (i.e. things won't be reordered and
    the original gamma generated list is ordered) this may not affect the
    important pyrate stuff anyway, but might affect gen_thumbs.py
    going to assume base_ifg_paths is ordered correcly
    """
    usage = 'Usage: python run_prepifg.py <config file>'

    def _convert_dem_inc_ele(params, base_ifg_paths):
        PROCESSOR = params[cf.PROCESSOR]  # roipac or gamma
        base_ifg_paths.append(params[cf.DEM_FILE])
        if PROCESSOR == GAMMA:
            if params[cf.APS_INCIDENCE_MAP]:
                base_ifg_paths.append(params[cf.APS_INCIDENCE_MAP])
            if params[cf.APS_ELEVATION_MAP]:
                base_ifg_paths.append(params[cf.APS_ELEVATION_MAP])
        return base_ifg_paths

    if params:
        base_ifg_paths = cf.original_ifg_paths(params[cf.IFG_FILE_LIST])
        LUIGI = params[cf.LUIGI]  # luigi or no luigi
        if LUIGI:
            raise cf.ConfigException('params can not be provided with luigi')
        base_ifg_paths = _convert_dem_inc_ele(params, base_ifg_paths)
    else:  # if params not provided read from config file
        if (not params) and ((len(sys.argv) == 1)
                or (sys.argv[1] == '-h' or sys.argv[1] == '--help')):
            print(usage)
            return
        base_ifg_paths, _, params = cf.get_ifg_paths(sys.argv[1])
        LUIGI = params[cf.LUIGI]  # luigi or no luigi
        raw_config_file = sys.argv[1]
        base_ifg_paths = _convert_dem_inc_ele(params, base_ifg_paths)

    PROCESSOR = params[cf.PROCESSOR]  # roipac or gamma

    if mpiops.size > 1:
        LUIGI = False
        params[cf.PARALLEL] = False

    if LUIGI:
        log.info("Running luigi prepifg")
        luigi.configuration.LuigiConfigParser.add_config_path(
            pythonify_config(raw_config_file))
        luigi.build([PrepareInterferograms()], local_scheduler=True)
    else:
        log.info("Running serial prepifg")
        process_base_ifgs_paths = \
            np.array_split(base_ifg_paths, mpiops.size)[mpiops.rank]
        if PROCESSOR == ROIPAC:
            roipac_prepifg(process_base_ifgs_paths, params)
        elif PROCESSOR == GAMMA:
            gamma_prepifg(process_base_ifgs_paths, params)
        else:
            raise prepifg.PreprocessError('Processor must be Roipac(0) or '
                                          'Gamma(1)')
    log.info('Finished prepifg')


def roipac_prepifg(base_ifg_paths, params):
    log.info("Running roipac prepifg")
    xlooks, ylooks, crop = cf.transform_params(params)
    dem_file = os.path.join(params[cf.ROIPAC_RESOURCE_HEADER])
    projection = roipac.parse_header(dem_file)[ifc.PYRATE_DATUM]
    dest_base_ifgs = [os.path.join(
        params[cf.OUT_DIR], os.path.basename(q).split('.')[0] + '.tif')
                      for q in base_ifg_paths]
    for b, d in zip(base_ifg_paths, dest_base_ifgs):
        header_file = "%s.%s" % (b, ROI_PAC_HEADER_FILE_EXT)
        header = roipac.manage_header(header_file, projection)
        write_geotiff(header, b, d, nodata=params[cf.NO_DATA_VALUE])
    prepifg.prepare_ifgs(
        dest_base_ifgs, crop_opt=crop, xlooks=xlooks, ylooks=ylooks)


def gamma_prepifg(base_unw_paths, params):
    log.info("Running gamma prepifg")
    parallel = params[cf.PARALLEL]

    # dest_base_ifgs: location of geo_tif's
    if parallel:
        print('running gamma in parallel with {} '
              'processes'.format(params[cf.PROCESSES]))
        dest_base_ifgs = Parallel(n_jobs=params[cf.PROCESSES], verbose=50)(
            delayed(gamma_multiprocessing)(p, params)
            for p in base_unw_paths)
    else:
        dest_base_ifgs = [gamma_multiprocessing(b, params)
                          for b in base_unw_paths]
    ifgs = [prepifg.dem_or_ifg(p) for p in dest_base_ifgs]
    xlooks, ylooks, crop = cf.transform_params(params)
    userExts = (params[cf.IFG_XFIRST], params[cf.IFG_YFIRST],
                params[cf.IFG_XLAST], params[cf.IFG_YLAST])
    exts = prepifg.getAnalysisExtent(crop, ifgs, xlooks, ylooks,
                                     userExts=userExts)
    thresh = params[cf.NO_DATA_AVERAGING_THRESHOLD]
    if parallel:
        Parallel(n_jobs=params[cf.PROCESSES], verbose=50)(
            delayed(prepifg.prepare_ifg)(p,
                   xlooks, ylooks, exts, thresh, crop)
            for p in dest_base_ifgs)
    else:
        [prepifg.prepare_ifg(i, xlooks, ylooks, exts,
                             thresh, crop) for i in dest_base_ifgs]


def gamma_multiprocessing(b, params):
    dem_hdr_path = params[cf.DEM_HEADER_FILE]
    SLC_DIR = params[cf.SLC_DIR]
    mkdir_p(params[cf.OUT_DIR])
    header_paths = gamma_task.get_header_paths(b, slc_dir=SLC_DIR)
    combined_headers = gamma.manage_headers(dem_hdr_path, header_paths)

    f, e = os.path.basename(b).split('.')
    if e != (params[cf.APS_INCIDENCE_EXT] or params[cf.APS_ELEVATION_EXT]):
        d = os.path.join(
            params[cf.OUT_DIR], f + '.tif')
    else:
        d = os.path.join(
            params[cf.OUT_DIR], f + '_' + e + '.tif')
        # TODO: implement incidence class here
        combined_headers['FILE_TYPE'] = 'Incidence'

    write_geotiff(combined_headers, b, d, nodata=params[cf.NO_DATA_VALUE])
    return d
