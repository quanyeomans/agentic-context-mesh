## Related Work
Initially, Operators were introduced by a blog post on the CoreOS Blog. This article provides a rough overview of what operators are, why the concept has been developed and how they are built. The insights of this article are mainly used for the definition of operators in this document. As the blog post only provided a concise overview, additional terms as capabilities, security and additional concepts are described more in-depth in this document.

The Operator Pattern as a concept is described in the Kubernetes documentation and therefore provides an overview of how an example operator could do and provides starting points for writing an operator [1].

The Book “Kubernetes Operators” [2] provides a comprehensive overview about operators, which problems they solve and the different methods to develop them. Definitions made in this book flowed into this document. The same applies to the Book “Kubernetes Patterns” (Ibryam, 2019), which provides more technical and conceptual insights to operators. Definitions made in these books were summarized in this document (to provide a common declaration of operators).

Michael Hausenblas and Stefan Schimanski [3] wrote a book about Programming Kubernetes, which provides deeper insights into client-go, custom resources, but also about writing operators.

Google provided a blog post about best practices for building Kubernetes Operators and stateful apps. Some of the advisories of this post take place in the best practices section of the whitepaper [4].

Many documents describe capability levels (also known as maturity levels) of operators. Since there could be cases where an operator that supports all features that fall on the highest capability level but does not support some lower level features, this document chooses to cover “capabilities” rather than “capability levels”. The capabilities required for each capability level, however, are taken into consideration [5].

The CNCF TAG Security spent a lot of effort to add security related topics to this whitepaper. As the content of this whitepaper should mostly cover operator-related security measures, they wrote a cloud native security whitepaper which is a very useful source when dealing with cloud native security [6].

### References

\[1\] https://kubernetes.io/docs/concepts/extend-kubernetes/operator/
\[2\] Dobies, J., & Wood, J. (2020). Kubernetes Operators. O'Reilly.
\[3\] Michael Hausenblas and Stefan Schimanski, Programming Kubernetes: Developing Cloud-Native Applications, First edition. (Sebastopol, CA: O’Reilly Media, 2019).
\[4\] https://cloud.google.com/blog/products/containers-kubernetes/best-practices-for-building-kubernetes-operators-and-stateful-apps
\[5\] Operator Framework. Retrieved 11 2020, 24, from https://operatorframework.io/operator-capabilities/,
https://github.com/cloud-ark/kubeplus/blob/master/Guidelines.md
\[6\] https://github.com/cncf/tag-security/blob/main/community/resources/security-whitepaper/v2/cloud-native-security-whitepaper.md