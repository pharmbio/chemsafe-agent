from .utils import predict_endpoint


def AHR_agonists(smiles_input, output_name=None):
    '''Predict aryl hydrocarbon receptor (AhR) agonism with the RiskMix conformal model.

    Parameters:
    ---------
    smiles_input (str or list): A SMILES string, a comma-separated string of SMILES, a list of SMILES, or a path to a CSV/TSV file with a 'smiles' column.
    output_name (str, optional): Absolute path for the results CSV. Defaults to the conversation's results folder; pass prepare_output_path("name.csv") to choose the filename.

    Returns:
    ----------
    results (dict or str): A dict for a single compound, otherwise the path to the results CSV in the conversation's results folder.
    '''

    confidence = 0.8
    return predict_endpoint("AHR_agonists", smiles_input, confidence, output_name)
