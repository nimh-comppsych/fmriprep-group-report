from pathlib import Path
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from bs4.element import Tag
from bids import BIDSLayout
from bids.tests import get_test_data_path
from ._html_snippets import html_head, html_foot, reviewer_initials, nav
import json
import click

layout = BIDSLayout(Path(get_test_data_path()) / 'synthetic')

def parse_report(report_path):
    """
    Parse an fmriprep report and return a dataframe with one row per image.
    Parameters
    ----------
    report_path : str
        Path to an existing fmriprep report html

    Returns
    -------
    report_elements : Pandas.DataFrame
        DataFrame of the images in the report and parsable metadata about those.
    """
    report_path = Path(report_path)
    soup = BeautifulSoup(report_path.read_text(), 'html.parser')
    file_divs = soup.findAll(attrs={'class':'svg-reportlet'})
    report_elements = []
    prev_run_title = np.nan
    for fd in file_divs:
        fig_path = fd.get('src', fd.get('data'))
        row = layout.parse_file_entities(fig_path, config=['bids', 'derivatives'])
        row['path'] = fig_path
        row['filename'] = Path(fig_path).parts[-1]
        elem_identifier = f"Parent div of {fig_path}"

        # Run titles should be hierarchical, if the current object doesn't have one
        # then it should be ok to use the previous one
        run_titles = fd.parent.findAll("h3", attrs={"class": "run-title"})
        try:
            row['run_title'] = _unique_retrieval(run_titles, elem_identifier, 'run-title')
            prev_run_title = row['run_title']
        except ValueError:
            row['run_title'] = prev_run_title

        # Some elements aren't in divs so the parent returns the whole document
        # in this case, loop through previous elements till we find one with an elem-caption class
        try:
            elem_captions = fd.parent.findAll("p", attrs={"class": "elem-caption"})
            row['elem_caption'] = _unique_retrieval(elem_captions, elem_identifier, 'elem-caption')
        except ValueError:
            for prev in fd.previous_siblings:
                if isinstance(prev, Tag) and prev.has_attr('class') and prev.get('class', '') == 'elem-caption':
                    row['elem_caption'] = prev.text

        report_elements.append(row)
    report_elements = pd.DataFrame(report_elements)
    # make a report type column, in general, this will just be the desc field, but some images don't have that
    report_elements['report_type'] = report_elements.desc
    dseg_ind = report_elements.report_type.isnull() & (report_elements.suffix == 'dseg')
    report_elements.loc[dseg_ind, 'report_type'] = report_elements.loc[dseg_ind, 'suffix']
    if 'space' in report_elements.columns:
        t1w_ind = report_elements.report_type.isnull() & report_elements.space.notnull()
        report_elements.loc[t1w_ind, 'report_type'] = report_elements.loc[t1w_ind, 'space']
    return report_elements


def _unique_retrieval(element_list, elem_identifier, search_identifier):
    """
    Get the text from a ResultSet that is expected to have a length of 1.
    Parameters
    ----------
    element_list :  bs4.element.ResultSet
        Result of a `findAll`
    elem_identifier : str
        An identifier for the element being searched, used in error messages.
    search_identifier : str
        An identifier for the thing being searched for, used in error messages.
    Returns
    -------
    str
        Text for the single matching element if one was found.
    """
    if len(element_list) > 1:
        raise ValueError(f"{elem_identifier} has more than one {search_identifier}.")
    elif len(element_list) == 0:
        raise ValueError(f"{elem_identifier} has no {search_identifier}.")
    else:
        return element_list[0].text


def _make_report_snippet(row):
    """
    Make a report snippet from a row generated by parse report.
    Parameters
    ----------
    row : dict
        Dictionary of report metadata for an svg from an fmriprep report.

    Returns
    -------
    snippet : str
        HTML snippet for the report image.
    """
    id_blacklist = ['path', 'run_title', 'elem_caption', 'extension', 'filename']
    header_blacklist = id_blacklist + ['desc', 'report_type', 'idx', 'chunk']
    id_ents = {k:v for k,v in row.items() if k not in id_blacklist}
    # needed for scripting to update counts as you scroll around
    id_ents['been_on_screen'] = False
    header_ents = {k:v for k,v in row.items() if k not in header_blacklist}

    header_vals = [f'{k} <span class="bids-entity">{v}</span>"' for k,v in header_ents.items() if pd.notnull(v)]
    header = "<h2> " + ', '.join(header_vals) + "</h2>"
    snippet = f"""
    <div id="id-{row['idx']}_filename-{row['filename'].split('.')[0]}">
      <script type="text/javascript">
        var subj_qc = {json.dumps(id_ents)}
      </script>
      {header}
      <div class="radio">
        <label><input type="radio" name="inlineRadio{row['idx']}" id="inlineRating1" value="1" onclick="qc_update({row['idx']}, 'report', this.value)"> Good </label>
        <label><input type="radio" name="inlineRadio{row['idx']}" id="inlineRating0" value="0" onclick="qc_update({row['idx']}, 'report', this.value)"> Bad</label>
      </div>
      <p> Notes: <input type="text" id="box{row['idx']}" oninput="qc_update({row['idx']}, 'note', this.value)"></p>
      <object class="svg-reportlet" type="image/svg+xml" data="{row['path']}"> </object>
    </div>
    <script type="text/javascript">
      subj_qc["report"] = -1
      subjs.push(subj_qc)
    </script>
    """
    return snippet


@click.command()
@click.option('--reports_per_page', default=50,
              help='How many figures per page. If None, then put them all on a single page.')
@click.option('--path_to_figures', default='../../sub-{subject}/figures',
              help="Relative path from group/sub-{subject} to subject's figure directory")
@click.argument('fmriprep_output_path')
def make_report(fmriprep_output_path, reports_per_page=50, path_to_figures='../../sub-{subject}/figures'):
    """
    Make a consolidated report from an fmripep output directory.
    Parameters
    ----------
    fmriprep_output_path : str
        Path to the fmriprep output
    reports_per_page : int or None
        How many figures per page. If None, then put them all on a single page.
    path_to_figures : str
        Relative path from group/sub-{subject} to subject's figure directory
    """
    fmriprep_output_path = Path(fmriprep_output_path)
    group_dir = fmriprep_output_path / 'group'
    group_dir.mkdir(exist_ok=True)
    # parse all the subject reports
    report_paths = sorted(fmriprep_output_path.glob('**/sub-*.html'))
    reports = []
    for report_path in report_paths:
        if not 'figures' in report_path.parts:
            reports.append(parse_report(report_path))

            # symlink figures directory into place
            subject = layout.parse_file_entities(report_path)['subject']
            subj_group_dir = group_dir / f'sub-{subject}'
            subj_group_dir.mkdir(exist_ok=True)
            orig_fig_dir = path_to_figures.format(subject=subject)
            subj_group_fig_dir = subj_group_dir / 'figures'
            if not subj_group_fig_dir.is_symlink():
                subj_group_fig_dir.symlink_to(orig_fig_dir, target_is_directory=True)

    reports = pd.concat(reports).reset_index(drop=True)

    # make a consolidated report for each report type
    for report_type, rtdf in reports.groupby('report_type'):
        rtdf = rtdf.copy().reset_index(drop=True)
        rtdf = rtdf.reset_index().rename(columns={'index': 'idx'})
        if reports_per_page is None:
            rtdf['chunk'] = 0
        else:
            rtdf['chunk'] = rtdf.idx // reports_per_page
        for chunk, cdf in rtdf.groupby('chunk'):
            consolidated_path = group_dir / f'consolidated_{report_type}_{chunk:03d}.html'
            lines = '\n'.join([_make_report_snippet(row) for row in cdf.to_dict('records')])

            rpt_text = '\n'.join([html_head,
                                  nav,
                                  reviewer_initials,
                                  lines,
                                  html_foot])
            consolidated_path.write_text(rpt_text)

