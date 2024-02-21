import argparse
import time
from argparse import ArgumentParser
from typing import Any

import pyarrow as pa
from data_processing.ray import (
    AbstractTableTransformRuntimeFactory,
    DefaultTableTransformRuntime,
    TransformLauncher,
)
from data_processing.transform import AbstractTableTransform

"""
Reference https://github.ibm.com/ai-foundation/foundation-model-stack/blob/main/preprocessing/ray/doc_quality_annotation/doc_annotation_actor.py
"""


class DocQualityTransform(AbstractTableTransform):
    """
    Implements various docuement quality metrics to documents in a pyarrow Table.
    """

    def __init__(self, config: dict):
        """
        Initialize based on the dictionary of configuration information.
        This is generally called with configuration parsed from the CLI arguments defined
        by the companion runtime, NOOPTransformRuntime.  If running inside the RayMutatingDriver,
        these will be provided by that class with help from the RayMutatingDriver.
        """
        from transforms.language.doc_quality.perplexity import KenLMModel
        super().__init__(config)
        self.warning_issued = False
        ft_lang = config["ft_lang"]
        strip_accent = True
        self.klm = KenLMModel.from_pretrained(model_path="/Docfilter/lm_sp/", language=ft_lang, strip_accent=strip_accent)
        if "drop_column_if_existed" in config:
            self.drop_column_if_existed = config["drop_column_if_existed"]
        else:
            self.drop_column_if_existed = True

    def transform(self, table: pa.Table) -> list[pa.Table]:

        from transforms.language.doc_quality.doc_Gopher_statistics import (
            compute_average_japanese_sentence_length,
            compute_bullet_point_ellipsis_alphabet_word_ratio,
            compute_word_statistics,
            contains_common_English_words,
            find_first_japanese_alphabet_position,
        )
        from doc_c4_statistics import (
            c4_contain_pattern_ratio,
            c4_contains_ldnoobw_words,
            c4_load_ldnoobw_words,
            c4_sentence_count,
        )

        """
        Put Transform-specific to convert one Table to another Table.
        This implementation makes no modifications so effectively implements a copy of the input parquet to the output folder, without modification.
        """
        new_columns = [
            "docq_total_words",
            "docq_mean_word_len",
            "docq_symbol_to_word_ratio",
            "docq_sentence_count",
            "docq_lorem_ipsum_ratio",
            "docq_curly_bracket_ratio",
            "docq_contain_bad_word",
            "docq_avg_ja_sentence_len",
            "docq_first_ja_alphabet_pos",
            "ibmkenlm_docq_perplex_score",
            "docq_contain_common_en_words",
            "docq_bullet_point_ratio",
            "docq_ellipsis_line_ratio",
            "docq_alphabet_word_ratio",
        ]
        for column in new_columns:
            if column in table.column_names:
                if self.drop_column_if_existed:
                    if not self.warning_issued:
                        # print(f"WARNING: drop existing column {column}. {input_parquet_path}")
                        print(f"WARNING: drop existing column {column}")
                        self.warning_issued = True
                    table = table.drop(column)
                else:
                    print(
                        f"ERROR: existing column {column} found and drop_column_if_existed is false. "
                        f"Terminating..."
                    )
                    exit(-1)

        docq_total_words = []
        docq_mean_word_len = []
        docq_symbol_to_word_ratio = []
        docq_sentence_count = []
        docq_curly_bracket_ratio = []
        docq_lorem_ipsum_ratio = []
        docq_contain_bad_word = []
        docq_bullet_point_ratio = []
        docq_ellipsis_line_ratio = []
        docq_alphabet_word_ratio = []
        docq_contain_common_en_words = []
        docq_perplex_score = []
        if self.ft_lang == "ja":
            # for japanese language, add 2 extra columns for 2 heuristic rules:
            docq_avg_ja_sentence_len = []
            docq_first_ja_alphabet_pos = []

        for text in table[self.col_name].to_pylist():
            total_words, mean_word_len, symbol_to_word_ratio = compute_word_statistics(text)
            docq_total_words.append(total_words)
            docq_mean_word_len.append(mean_word_len)
            docq_symbol_to_word_ratio.append(symbol_to_word_ratio)

            docq_sentence_count.append(c4_sentence_count(text, ft_lang=self.ft_lang))

            docq_lorem_ipsum_ratio.append(
                c4_contain_pattern_ratio(text, pattern="lorem ipsum", ft_lang=self.ft_lang, normalize_text=True)
            )
            curly_bracket_ratio = 0.0
            for sign in ["{", "}"]:
                curly_bracket_ratio += c4_contain_pattern_ratio(
                    text, pattern=sign, ft_lang=self.ft_lang, normalize_text=False
                )
            docq_curly_bracket_ratio.append(curly_bracket_ratio)
            docq_contain_bad_word.append(c4_contains_ldnoobw_words(text, self.re_pattern))

            (
                bullet_point_ratio,
                ellipsis_line_ratio,
                alphabet_word_ratio,
            ) = compute_bullet_point_ellipsis_alphabet_word_ratio(text)
            docq_bullet_point_ratio.append(bullet_point_ratio)
            docq_ellipsis_line_ratio.append(ellipsis_line_ratio)
            docq_alphabet_word_ratio.append(alphabet_word_ratio)

            docq_contain_common_en_words.append(contains_common_English_words(text, self.ft_lang))

            docq_perplex_score.append(self.klm.get_perplexity(text))

            if self.ft_lang == "ja":
                docq_avg_ja_sentence_len.append(compute_average_japanese_sentence_length(text))
                docq_first_ja_alphabet_pos.append(find_first_japanese_alphabet_position(text))

        table = table.append_column("docq_total_words", pa.array(docq_total_words))
        table = table.append_column("docq_mean_word_len", pa.array(docq_mean_word_len))
        table = table.append_column("docq_symbol_to_word_ratio", pa.array(docq_symbol_to_word_ratio))
        table = table.append_column("docq_sentence_count", pa.array(docq_sentence_count))
        table = table.append_column("docq_lorem_ipsum_ratio", pa.array(docq_lorem_ipsum_ratio))
        table = table.append_column("docq_curly_bracket_ratio", pa.array(docq_curly_bracket_ratio))
        table = table.append_column("docq_contain_bad_word", pa.array(docq_contain_bad_word))
        table = table.append_column("docq_bullet_point_ratio", pa.array(docq_bullet_point_ratio))
        table = table.append_column("docq_ellipsis_line_ratio", pa.array(docq_ellipsis_line_ratio))
        table = table.append_column("docq_alphabet_word_ratio", pa.array(docq_alphabet_word_ratio))
        table = table.append_column("docq_contain_common_en_words", pa.array(docq_contain_common_en_words))
        table = table.append_column("ibmkenlm_docq_perplex_score", pa.array(docq_perplex_score))

        if self.ft_lang == "ja":
            table = table.append_column("docq_avg_ja_sentence_len", pa.array(docq_avg_ja_sentence_len))
            table = table.append_column("docq_first_ja_alphabet_pos", pa.array(docq_first_ja_alphabet_pos))

        return [table]


class DocQualityTransformRuntimeFactory(AbstractTableTransformRuntimeFactory):

    """
    Provides support for configuring and using the associated Transform class include
    configuration with CLI args and combining of metadata.
    """

    def __init__(self):
        super().__init__(runtime_class=DefaultTableTransformRuntime, transformer_class=DocQualityTransform)
        self.params = {}

    def add_input_params(self, parser: ArgumentParser) -> None:
        """
        Add Transform-specific arguments to the given  parser.
        This will be included in a dictionary used to initialize the NOOPTransform.
        By convention a common prefix should be used for all transform-specific CLI args
        (e.g, noop_, pii_, etc.)
        """
        parser.add_argument("-f", "--ft_lang", default="en")
        parser.add_argument("-dr", "--drop_column_if_existed", default=False, help="drop columns if existed")

    def apply_input_params(self, args: argparse.Namespace) -> bool:
        """
        Validate and apply the arguments that have been parsed
        :param args: user defined arguments including at least, but perhaps more,
        arguments as defined by add_input_arguments().
        :return: True, if validate pass or False otherwise
        """
        self.params["ft_lang"] = args.ft_lang
        self.params["drop_column_if_existed"] = args.drop_column_if_existed
        return True

    def get_input_params_metadata(self) -> dict[str, Any]:
        """
        get input parameters for job_input_params in metadata
        :return:
        """
        return self.params


if __name__ == "__main__":
    # create launcher
    launcher = TransformLauncher(
        name="DocQualityTransform", transform_runtime_factory=DocQualityTransformRuntimeFactory()
    )
    # create parameters

    # launch
    launcher.launch()
