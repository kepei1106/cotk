"""
Containing some classes and functions about precision and recall evaluating results of models.
"""
from typing import List, Dict, Any
from itertools import chain
import numpy as np
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
from .metric import MetricBase
from ..hooks import hooks

class _PrecisionRecallMetric(MetricBase):
	"""Base class for precision recall metrics. This is an abstract class.

	Arguments:
		{ARGUMENTS}
	Attributes:
		res_prefix (str): Prefix added to the front of each key
					in the result dict of `close`.
	"""

	ARGUMENTS = """
		{MetricBase.DATALOADER_ARGUMENTS}
		generated_num_per_context (int): The number of sentences generated per context.
		candidate_allvocabs_key (str, optional): The key of reference sentences. Default: ``candidate_allvocabs``.
		multiple_gen_key (str, optional):
			The key of multiple generated sentences. Default: ``multiple_gen``."""


	def __init__(self, name: str, version: int, \
				 dataloader: "LanguageProcessing", \
				 generated_num_per_context: int, \
				 candidate_allvocabs_key: str = 'candidate_allvocabs', \
				 multiple_gen_key: str = 'multiple_gen'):
		super().__init__(name, version)
		self.dataloader = dataloader
		self.candidate_allvocabs_key = candidate_allvocabs_key
		self.multiple_gen_key = multiple_gen_key
		self.generated_num_per_context = generated_num_per_context
		self.prec_list = []
		self.rec_list = []
		self.res_prefix = ""

	def _score(self, gen, reference):
		'''This function is called by :func:`forward`.

		Arguments:
			gen (list): list of generated word ids.
			reference (list): list of word ids of a reference.

		Returns:
			int: score \in [0, 1].
		'''
		raise NotImplementedError( \
			"This function should be implemented by subclasses.")

	def forward(self, data: Dict[str, Any]):
		'''Processing a batch of data.

		Arguments:
			data (dict): A dict at least contains the following keys:

				* **data[candidate_allvocabs_key]** (list, :class:`numpy.ndarray`):
				  A 3-d jagged list of index. Multiple reference sentences for a single context.
				  Does not contain start token (eg: ``<go>``) and end token (eg: ``<eos>``).
				  Size: ``[batch_size, ~sentence_num, ~word_num]``, where "~" means different sizes
				  in this dimension is allowed.
				* **data[multiple_gen_key]** (list, :class:`numpy.ndarray`):
				  A 3-d jagged or padded array.
				  Sentences generated by model. Contains end token (eg: ``<eos>``),
				  but without start token (eg: ``<go>``).
				  Size: ``[batch_size, generated_num_per_context, ~gen_sentence_length]``,
				  where "~" means different sizes in this dimension is allowed.

				Here is an example for data:

					>>> # all_vocab_list = ["<pad>", "<unk>", "<go>", "<eos>", "I", "have",
					>>> #   "been", "to", "China"]
					>>> data = {
					...     candidate_allvocabs_key: [[[4], [5,6]], [[4,5,6]]],
					...	    multiple_gen_key: [[[5,6,3]], [[4,5,7,3], [8,3]]]
					... }

		'''
		super().forward(data)
		candidate_allvocabs = data[self.candidate_allvocabs_key]
		multiple_gen = data[self.multiple_gen_key]

		if not isinstance(candidate_allvocabs, (np.ndarray, list)):
			raise TypeError("Unknown type for candidate_allvocabs.")
		if not isinstance(multiple_gen, (np.ndarray, list)):
			raise TypeError("Unknown type for multiple_gen")

		references = [[self.dataloader.trim_in_ids(cand[1:]) for cand in inst] \
					  for inst in candidate_allvocabs]
		gens = [[self.dataloader.trim_in_ids(cand) for cand in inst] \
					  for inst in multiple_gen]

		if len(references) != len(gens):
			raise ValueError("Batch num is not matched.")

		for line in gens:
			if len(line) != self.generated_num_per_context:
				raise ValueError(\
					"Number of geneated sentences per context does not equal to\
					the specified `generated_num_per_context`")

		self._hash_unordered_list(list(chain(*references)))
		for reference, gen in zip(references, gens):
			# pylint: disable=no-member
			matrix = np.zeros((len(reference), len(gen)), dtype=np.float32)
			for i, single_ref in enumerate(reference):
				for j, single_gen in enumerate(gen):
					matrix[i][j] = self._score(single_gen, single_ref)
			self.prec_list.append(float(np.sum(np.max(matrix, 0))) / len(gen))
			self.rec_list.append(float(np.sum(np.max(matrix, 1))) / len(reference))

	@hooks.hook_metric_close
	def close(self) -> Dict[str, Any]:
		'''Return a dict which contains

			* ``res_prefix`` **precision**: average precision.
			* ``res_prefix`` **recall**: average recall.
			* ``res_prefix`` **hashvalue**: hash value for precision & recall metric, same hash value stands
			  for same evaluation settings.

		'''
		if (not self.prec_list) or (not self.rec_list):
			raise RuntimeError("The metric has not been forwarded data correctly.")
		res = super().close()
		res.update({'{} precision'.format(self.res_prefix): np.average(self.prec_list), \
				'{} recall'.format(self.res_prefix): np.average(self.rec_list), \
				'{} hashvalue'.format(self.res_prefix): self._hashvalue()})
		return res

class BleuPrecisionRecallMetric(_PrecisionRecallMetric):
	'''Metric for calculating sentence BLEU precision and recall.

	References:
		[1] Zhao, T., Zhao, R., & Eskenazi, M. (2017). Learning discourse-level diversity
		for neural dialog models using conditional variational autoencoders.
		arXiv preprint arXiv:1703.10960.

	Arguments:
		{_PrecisionRecallMetric.ARGUMENTS}
		ngram (int): Specifies using BLEU-ngram.

	Here is an exmaple:

		>>> dl = cotk.dataloader.UbuntuCorpus('resources://Ubuntu_small')
		>>> candidate_allvocabs_key = 'candidate_allvocabs'
		>>> multiple_gen_key='multiple_gen'
		>>> metric = cotk.metric.BleuPrecisionRecallMetric(dl, 2, 2)
		>>> data = {
		...	    candidate_allvocabs_key: [[[10, 64, 851], [10, 48, 851]]],
		...	    # candidate_allvocabs_key: [[["I", "like", "python"], ["I", "use", "python"]]],
		...     multiple_gen_key: [[[10, 64, 479, 3], [10, 48, 2019, 3]]],
		...     # multiple_gen_key: [[["I", "like", "java", "<eos>"], ["I", "use", "PHP", "<eos>"]]],
		... }
		>>> metric.forword(data)
		>>> metric.close()
		{'BLEU-2 precision': 0.12909944355487823,
 		 'BLEU-2 recall': 0.12909944355487823,
 		 'BLEU-2 hashvalue': '1652cd40276078ec8722d367f18008bf14053572ac15ce10e270eb41eae34bbf'}
	'''

	_name = 'BleuPrecisionRecallMetric'
	_version = 2

	@hooks.hook_metric
	def __init__(self, dataloader: "LanguageProcessing", \
				 ngram: int, \
				 generated_num_per_context: int, \
				 candidates_allvocabs_key: str = 'candidate_allvocabs', \
				 multiple_gen_key: str = 'multiple_gen'):
		super().__init__(self._name, self._version, \
				dataloader, generated_num_per_context, candidates_allvocabs_key, \
				multiple_gen_key)
		self.ngram = ngram
		self.weights = [1 / ngram] * ngram
		self.res_prefix = 'BLEU-{}'.format(ngram)
		self._hash_ordered_data([ngram, generated_num_per_context])

	def _replace_unk(self, _input, _target=-1):
		'''Auxiliary function for replacing the unknown words:

		Arguments:
			_input (list): the references or hypothesis.
			_target: the target word index used to replace the unknown words.

		Returns:

			* list: processed result.
		'''
		output = []
		for ele in _input:
			output.append(_target if ele == self.dataloader.unk_id else ele)
		return output

	def _score(self, gen: List[int], reference: List[int]) -> float:
		'''Return a BLEU score \in [0, 1] to calculate BLEU-ngram precision and recall.

		Arguments:
			gen (list): list of generated word ids.
			reference (list): list of word ids of a reference.

		Here is an Example:

			>>> gen = [4,5]
			>>> reference = [5,6]
			>>> self._score(gen, reference)
			0.150 # assume self.weights = [0.25,0.25,0.25,0.25]
		'''
		gen = self._replace_unk(gen)
		return sentence_bleu([reference], gen, self.weights, SmoothingFunction().method1)

class EmbSimilarityPrecisionRecallMetric(_PrecisionRecallMetric):
	'''Metric for calculating cosine similarity precision and recall.

	References:
		[1] Zhao, T., Zhao, R., & Eskenazi, M. (2017). Learning discourse-level diversity
		for neural dialog models using conditional variational autoencoders.
		arXiv preprint arXiv:1703.10960.

	Arguments:
		{_PrecisionRecallMetric.ARGUMENTS}
		word2vec (dict): Maps a word (str) to its pretrained embedding (:class:`numpy.ndarray` or list)
		mode (str): Specifies the operation that computes the bag-of-word representation. \
			Must be ``avg`` or ``extrema``:

			* ``avg`` : element-wise average word embeddings.
			* ``extrema`` : element-wise maximum word embeddings.

	Here is an exmaple:

		>>> dl = cotk.dataloader.UbuntuCorpus('resources://Ubuntu_small')
		>>> candidate_allvocabs_key = 'candidate_allvocabs'
		>>> multiple_gen_key='multiple_gen'
		>>> wordvector = cotk.wordvector.Glove()
		>>> metric = cotk.metric.EmbSimilarityPrecisionRecallMetric(dl, wordvector.load_dict(dl.all_vocab_list()), 'avg', 2)
		>>> data = {
		...	    candidate_allvocabs_key: [[[10, 64, 851], [10, 48, 851]]],
		...	    # candidate_allvocabs_key: [[["I", "like", "python"], ["I", "use", "python"]]],
		...     multiple_gen_key: [[[10, 64, 479, 3], [10, 48, 2019, 3]]],
		...     # multiple_gen_key: [[["I", "like", "java", "<eos>"], ["I", "use", "PHP", "<eos>"]]],
		... }
		>>> metric.forword(data)
		>>> metric.close()
		>>> # metric.close() returns a dict like this.
		>>>	# {'avg-bow precision': 0.0,
		>>>	# 'avg-bow recall': 0.0,
		>>>	# 'avg-bow hashvalue': '5abaaa9a8e709b3f05467e3f6d0e27c6cc904fceebd3accb3b768928595e729a'}
	'''

	_name = 'EmbSimilarityPrecisionRecallMetric'
	_version = 2

	@hooks.hook_metric
	def __init__(self, dataloader: "LanguageProcessing", \
				 word2vec: Dict[str, Any], \
				 mode: str, \
				 generated_num_per_context: int, \
				 candidates_allvocabs_key: str = 'candidate_allvocabs', \
				 multiple_gen_key: str = 'multiple_gen'):
		super().__init__(self._name, self._version, dataloader, generated_num_per_context, \
			candidates_allvocabs_key, multiple_gen_key)
		if not isinstance(word2vec, dict):
			raise ValueError("word2vec has invalid type")
		if word2vec:
			embed_shape = np.array(list(word2vec.values())).shape
			if len(embed_shape) != 2 or embed_shape[1] == 0:
				raise ValueError("word embeddings have inconsistent embedding size or are empty")
		if mode not in ['avg', 'extrema']:
			raise ValueError("mode should be 'avg' or 'extrema'.")
		self.word2vec = word2vec
		self.mode = mode
		self.res_prefix = '{}-bow'.format(mode)
		self._hash_ordered_data([mode, generated_num_per_context] + \
				[(word, list(emb)) for word, emb in self.word2vec.items()])

	def _score(self, gen: List[int], reference: List[int]) -> float:
		'''Return a cosine similarity score \in [0, 1] between two sentence embeddings to calculate cosine similarity \
		   precision and recall.

		Arguments:
			gen (list): list of generated word ids.
			reference (list): list of word ids of a reference.

		Here is an Example:

			>>> gen = [4,5]
			>>> reference = [5,6]
			>>> self._score(gen, reference)
			0.135 # assume self.mode = 'avg'
		'''
		gen_vec = []
		ref_vec = []
		for word in self.dataloader.convert_ids_to_tokens(gen):
			if word in self.word2vec:
				gen_vec.append(self.word2vec[word])
		for word in self.dataloader.convert_ids_to_tokens(reference):
			if word in self.word2vec:
				ref_vec.append(self.word2vec[word])
		if not gen_vec or not ref_vec:
			return 0
		if self.mode == 'avg':
			gen_embed = np.average(gen_vec, 0)
			ref_embed = np.average(ref_vec, 0)
		else:
			gen_embed = np.max(gen_vec, 0)
			ref_embed = np.max(ref_vec, 0)
		cos = np.sum(gen_embed * ref_embed) / \
			  np.sqrt(np.sum(gen_embed * gen_embed) * np.sum(ref_embed * ref_embed))
		norm = (cos + 1) / 2
		return norm
