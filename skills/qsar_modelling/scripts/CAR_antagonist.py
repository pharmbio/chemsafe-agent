from .utils import predict_endpoint


def CAR_antagonist(smiles_input, output_name=None):
    '''Predict constitutive androstane receptor (CAR) antagonism with the RiskMix conformal model.

    Parameters:
    ---------
    smiles_input (str or list): A SMILES string, a comma-separated string of SMILES, a list of SMILES, or a path to a CSV/TSV file with a 'smiles' column.
    output_name (str, optional): path for the results CSV. 

    Returns:
    ----------
    results (dict or str): A dict for a single compound, otherwise the path to the results CSV.
    '''

    confidence = 0.8
    return predict_endpoint("CAR_antagonist", smiles_input, confidence, output_name)
