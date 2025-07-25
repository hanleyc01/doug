import sys

import numpy
import torch
import torchhd
import torchhd.structures as struct

from language.lexer import KEYWORDS
from language.syntax import (
    LLBool,
    LLTerm,
    LLType,
    Level,
    LLLambda,
    LLDollar,
    LLLet,
    LLList,
    LLCaseBool,
    LLCaseList,
    LLAnn,
    LLApp,
    LLCons,
    LLConst,
    LLFalse,
    LLFunc,
    LLTuple,
    LLModal,
    LLTrue,
    LLCredit,
    LLNil,
    LLTupleConstr,
    LLProj,
    LLVar,
)


class EncodingEnvironment:
    """The encoding environment."""

    def __init__(self, dim: int, device=None) -> None:
        self.device = device
        self.dim = dim
        self.declarative_memory = struct.Memory()
        # TODO: add cleanup-memory
        self.cleanup_memory = None

        self.codebook: dict[str, torchhd.FHRRTensor] = {}
        self.init_codebook()
        # TODO: remove and replace with RHC embedding
        self.fractional_embed = torchhd.embeddings.FractionalPower(1, self.dim)

    def init_codebook(self) -> None:
        """Initialize the codebook of the encoding environment."""
        symbols = [
            symbol
            for symbol in torchhd.random(len(KEYWORDS), self.dim, vsa="FHRR")
        ]
        for key, symbol in zip(KEYWORDS, symbols):
            self.codebook[key] = symbol

        # Structural items are vector symbols in the codebook which are used
        # as roles in role-filler structure pairs in encoding the abstract syntax.
        # To see where it is used, and the particular fillers for the roles for
        # each syntactic item, see the encoding functions below.
        # Each structural item begins with the `#:`.
        structural_items = [
            "#:kind",
            "#:type",
            "#:level",
            "#:map",
            "#:dom",
            "#:codom",
            "#:left",
            "#:right",
            "#:from",
            "#:var",
            "#:to",
        ]
        structural_symbols = [
            symbol
            for symbol in torchhd.random(
                len(structural_items), self.dim, vsa="FHRR"
            )
        ]
        for item, symbol in zip(structural_items, structural_symbols):
            self.codebook[item] = symbol

    def encode_type(self, type_: LLType) -> torchhd.VSATensor:
        """Encode an `LLType` into a hypervector.

        :param LLType type_: The syntactic description of the type.
        :return: The encoded representation of the type.
        :rtype: torchhd.VSATensor
        """
        # Match on the dataclass
        match type_:
            # Boolean types
            # Returns (#:level * <level>) + (#:kind * "bool")
            case LLBool(level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["bool"]

                return self.codebook["#:level"].bind(
                    encoded_level
                ) + self.codebook["#:kind"].bind(kind)

            # Function types
            # Returns (#:kind * "->") + (#:type * ((#:dom * <rator>) + (#:codom * <rand>) )))
            case LLFunc(rator, rand):
                rator_encoded = self.encode_type(rator)
                rand_encoded = self.encode_type(rand)
                # TODO: add levels

                kind = self.codebook["->"]
                _type = rator_encoded.bind(
                    self.codebook["#:dom"]
                ) + rand_encoded.bind(self.codebook["#:codom"])

                return kind.bind(self.codebook["#:kind"]) + _type.bind(
                    self.codebook["#:type"]
                )

            # Tuple types
            # Returns
            # (#:kind * "*") + (#:type * ((#:right * <rhs>) + (#:left * <lhs>))))
            case LLTuple(lhs, rhs):
                lhs_encoded = self.encode_type(lhs)
                rhs_encoded = self.encode_type(rhs)
                # TODO: add levels

                kind = self.codebook["*"]
                _type = lhs_encoded.bind(
                    self.codebook["#:left"]
                ) + rhs_encoded.bind(self.codebook["#:right"])

                return kind.bind(self.codebook[":kind"]) + _type.bind(
                    self.codebook["#:type"]
                )

            # List types
            # (#:kind * "list") + (#:level * <level>) + (#:type * <type>)
            case LLList(type_arg, level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["list"]
                _type = self.encode_type(type_arg)

                return (
                    encoded_level.bind(self.codebook["#:level"])
                    + kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                )

            # "Modal" types: marks it as non-affine.
            # Returns
            # (#:kind * "!") + (#:level * <level>) + (#:type * <type>)
            case LLModal(type_arg, level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["!"]
                _type = self.encode_type(type_arg)

                return (
                    encoded_level.bind(self.codebook["#:level"])
                    + kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                )

            # Chit type
            # Returns
            # (#:kind * "#") + (#:level * <level>)
            case LLCredit(level):
                # TODO: change to unicode diamond
                encoded_level = self.encode_level(level)
                kind = self.codebook["#"]

                return encoded_level.bind(
                    self.codebook["#:level"]
                ) + kind.bind(self.codebook["#:kind"])
            case _:
                raise TypeError("ERROR: innapropriate argument type")

    def encode_constant(self, constant: LLConst) -> torchhd.VSATensor:
        """Encode an `LLConst` into a hypervector.

        :param LLConst constant: The constant to encode.
        :return: The result of the encoding.
        :rtype: torchhd.VSATensor
        """
        match constant:
            # Boolean constants
            # Returns
            # (#:kind * "true") + (#:level * <level>)
            case LLTrue(level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["true"]

                return encoded_level.bind(
                    self.codebook["#:level"]
                ) + kind.bind(self.codebook["#:kind"])

            # Returns
            # (#:kind * "false") + (#:level * <level>)
            case LLFalse(level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["false"]

                return encoded_level.bind(
                    self.codebook["#:level"]
                ) + kind.bind(self.codebook["#:kind"])

            # Boolean destructor
            # Returns
            # (#:kind * "case-bool") + (#:level * "level")
            case LLCaseBool(type_arg, level):
                _type = self.encode_type(type_arg)
                encoded_level = self.encode_level(level)
                kind = self.codebook["case-bool"]

                return (
                    _type.bind(self.codebook["#:type"])
                    + encoded_level.bind(self.codebook["#:level"])
                    + kind.bind(self.codebook["#:kind"])
                )

            # List destructor
            # Returns
            # (#:kind * "case-list") + (#:type * ( (#:from * <type_arg0>) + (#:to * <type_arg1>) )) + (#:level * <level>)
            case LLCaseList(type_arg0, type_arg1, level):
                encoded_level = self.encode_level(level)
                from_ = self.encode_type(type_arg0)
                to_ = self.encode_type(type_arg1)
                kind = self.codebook["case-list"]
                _type = from_.bind(self.codebook["#:from"]) + to_.bind(
                    self.codebook["#:to"]
                )

                return (
                    kind.bind(self.codebook["#:kind"])
                    + encoded_level.bind(self.codebook["#:level"])
                    + _type.bind(self.codebook["#:type"])
                )

            # List constructor
            # Returns
            # (#:kind * "cons") + (#:level * <level>) + (#:type * <type>)
            case LLCons(type_arg, level):
                encoded_level = self.encode_level(level)
                _type = self.encode_type(type_arg)
                kind = self.codebook["cons"]
                return (
                    kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                    + encoded_level.bind(self.codebook[":level"])
                )

            # Empty list constructor
            # Returns
            # (#kind * "nil") + (#:level * <level>) + (#:type * <type>)
            case LLNil(type_arg, level):
                encoded_level = self.encode_level(level)
                _type = self.encode_type(type_arg)
                kind = self.codebook["nil"]
                return (
                    kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                    + encoded_level.bind(self.codebook[":level"])
                )

            # Chit type constructor
            # Returns
            # (#:kind * "dollar") + (#:level * <level>)
            case LLDollar(level):
                encoded_level = self.encode_level(level)
                kind = self.codebook["dollar"]
                return kind.bind(self.codebook["#:kind"]) + encoded_level.bind(
                    self.codebook["#:level"]
                )

            # Affine tuple type constructor
            # Returns
            # (#:kind * "tuple") +  (#:level * <level>) +
            #   (#:type * ( (#:left * <left>) + (#:right + <right>) ))
            case LLTupleConstr(level, type_arg0, type_arg1):
                encoded_level = self.encode_level(level)
                left = self.encode_type(type_arg0)
                right = self.encode_type(type_arg1)
                _type = left.bind(self.codebook["#:left"]) + right.bind(
                    self.codebook["#:right"]
                )
                kind = self.codebook["tuple"]
                return (
                    encoded_level.bind(self.codebook["#:level"])
                    + kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                )
                # this is a test

            # Tuple type destructor
            # Returns
            # (#:kind * "pi") + (#:level * <level>) + (#:type + <type_arg>)
            case LLProj(level, type_arg):
                encoded_level = self.encode_level(level)
                _type = self.encode_type(type_arg)
                kind = self.codebook["pi"]

                return (
                    kind.bind(self.codebook["#:kind"])
                    + _type.bind(self.codebook["#:type"])
                    + encoded_level.bind(self.codebook["#:level"])
                )
            case _:
                raise TypeError("ERROR: inappropriate argument type")

    def encode_term(self, term: LLTerm) -> torchhd.VSATensor:
        """Encode an `LLTerm` into a hypervector.

        :param LLTerm term: The term to encode.
        :return: The result of the encoding.
        :rtype: torchhd.VSATensor
        """
        # Match on the form of the term
        match term:
            # Annotated variables
            # Returns
            # (#:kind * ":") + (#:type * <type_ann>) + (#:var * self.codebook[var])
            case LLAnn(var, type_ann):
                if var not in self.codebook:
                    self.codebook[var] = torchhd.random(
                        1, self.dim, vsa="FHRR"
                    )[0]
                _type = self.encode_type(type_ann)
                kind = self.codebook[":"]
                return (
                    _type.bind(self.codebook["#:typpe"])
                    + self.codebook[var].bind(self.codebook["#:var"])
                    + kind.bind(self.codebook["#:kind"])
                )
            case _:
                raise TypeError("ERROR; inappropriate argument type")

    def encode_level(self, level: Level) -> torchhd.VSATensor:
        """Encode a a `level` as an integer representation.

        :param Level level: The level to encode.
        :return: An encoded form of the level, using fractional binding.
        """
        return self.fractional_embed(torch.tensor([level]))
